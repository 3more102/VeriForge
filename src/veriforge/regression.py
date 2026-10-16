"""Named, seeded simulator regressions."""

from __future__ import annotations

import json
import re
import time
import tomllib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .project import Project, ProjectError
from .runner import run_simulation


_CASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MAX_CASES = 256
_MAX_SEED = 2**32 - 1


@dataclass(frozen=True)
class RegressionCase:
    name: str
    seed: int | None
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class RegressionManifest:
    name: str
    cases: tuple[RegressionCase, ...]


def load_regression(path: Path) -> RegressionManifest:
    """Load and validate a TOML regression manifest."""
    if not path.is_file():
        raise ProjectError(f"Regression manifest not found: {path}")
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectError(f"Cannot read regression manifest {path}: {exc}") from exc

    regression = config.get("regression", {})
    cases_value = config.get("case")
    if not isinstance(regression, dict):
        raise ProjectError("[regression] must be a TOML table")
    if not isinstance(cases_value, list) or not cases_value:
        raise ProjectError("regression.toml must contain at least one [[case]] table")
    if len(cases_value) > _MAX_CASES:
        raise ProjectError(f"A regression may contain at most {_MAX_CASES} cases")

    regression_name = str(regression.get("name", path.stem)).strip()
    if not _CASE_NAME.fullmatch(regression_name):
        raise ProjectError("[regression].name must contain only letters, digits, '.', '_' or '-'")

    cases: list[RegressionCase] = []
    seen: set[str] = set()
    for index, value in enumerate(cases_value, start=1):
        if not isinstance(value, dict):
            raise ProjectError(f"[[case]] entry {index} must be a TOML table")
        name = value.get("name")
        if not isinstance(name, str) or not _CASE_NAME.fullmatch(name):
            raise ProjectError(
                f"[[case]] entry {index} name must contain only letters, digits, '.', '_' or '-'"
            )
        if name in seen:
            raise ProjectError(f"Duplicate regression case name: {name}")
        seen.add(name)

        seed = value.get("seed")
        if seed is not None and (
            not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= _MAX_SEED
        ):
            raise ProjectError(f"Regression case {name}: seed must be an integer from 0 to {_MAX_SEED}")

        arguments = value.get("arguments", [])
        if not isinstance(arguments, list) or not all(
            isinstance(argument, str) and argument and "\0" not in argument
            for argument in arguments
        ):
            raise ProjectError(f"Regression case {name}: arguments must be non-empty strings")
        if seed is not None and any(argument.startswith("+veriforge_seed=") for argument in arguments):
            raise ProjectError(
                f"Regression case {name}: seed is supplied by the manifest; remove its duplicate plusarg"
            )
        cases.append(RegressionCase(name, seed, tuple(arguments)))

    return RegressionManifest(regression_name, tuple(cases))


def run_regression(project: Project) -> tuple[int, dict]:
    """Run each manifest case and persist an aggregate summary."""
    manifest = load_regression(project.regression_file)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started_clock = time.monotonic()
    case_results: list[dict] = []

    for case in manifest.cases:
        run_dir = project.root / ".veriforge" / "regression" / case.name
        run_dir.mkdir(parents=True, exist_ok=True)
        waveform_file = None
        if project.waveform_file is not None:
            relative_waveform = project.waveform_file.relative_to(project.root)
            waveform_file = run_dir / relative_waveform
            waveform_file.parent.mkdir(parents=True, exist_ok=True)
        case_project = replace(project, waveform_file=waveform_file)
        arguments = [*case.arguments]
        if case.seed is not None:
            arguments.append(f"+veriforge_seed={case.seed}")
        _, result = run_simulation(
            case_project,
            runtime_args=arguments,
            scenario=case.name,
            seed=case.seed,
            execution_dir=run_dir,
        )
        test_count = len(result.get("tests", []))
        passed_tests = sum(item.get("status") == "pass" for item in result.get("tests", []))
        case_results.append(
            {
                "name": case.name,
                "seed": case.seed,
                "run_id": result.get("run_id"),
                "status": result["status"],
                "test_count": test_count,
                "passed_tests": passed_tests,
                "log": f"logs/{result['simulator']}-{case.name}.log",
                "waveform": result.get("waveform"),
                "message": result.get("message"),
            }
        )

    statuses = [case["status"] for case in case_results]
    if all(status == "passed" for status in statuses):
        status = "passed"
    elif "failed" in statuses:
        status = "failed"
    else:
        status = "blocked"

    summary = {
        "name": manifest.name,
        "status": status,
        "started_at": started_at,
        "duration_seconds": round(time.monotonic() - started_clock, 3),
        "case_count": len(case_results),
        "passed_cases": sum(case["status"] == "passed" for case in case_results),
        "failed_cases": sum(case["status"] == "failed" for case in case_results),
        "blocked_cases": sum(case["status"] == "blocked" for case in case_results),
        "cases": case_results,
    }
    state_dir = project.root / ".veriforge"
    state_dir.mkdir(parents=True, exist_ok=True)
    temporary = state_dir / "regression.json.tmp"
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(state_dir / "regression.json")
    return ({"passed": 0, "failed": 1, "blocked": 2}[status]), summary
