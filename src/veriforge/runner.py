"""Verilator build/run adapter for small, reproducible simulations."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .project import Project


_VFTEST = re.compile(r"\bVFTEST:\s*([A-Za-z0-9_.:/-]+)\s+(PASS|FAIL)\b(?:\s+(.*))?", re.I)


def parse_test_markers(output: str) -> list[dict]:
    """Parse explicit test results; a later marker replaces an earlier one."""
    tests: dict[str, dict] = {}
    for match in _VFTEST.finditer(output):
        test_id, status, detail = match.groups()
        tests[test_id] = {
            "id": test_id,
            "status": status.lower(),
            "detail": (detail or "").strip(),
        }
    return [tests[key] for key in sorted(tests)]


def evaluate_run(compile_code: int | None, run_code: int | None, tests: list[dict]) -> tuple[str, str | None]:
    """Fail closed when compilation, execution, or test markers are missing."""
    if compile_code != 0:
        return "failed", "Verilator compilation failed."
    if run_code != 0:
        return "failed", "The simulation exited with a non-zero status."
    if not tests:
        return "failed", "The testbench exited successfully but emitted no VFTEST result markers."
    not_passed = [item["id"] for item in tests if item.get("status") != "pass"]
    if not_passed:
        return "failed", "Test failure or invalid status marker(s): " + ", ".join(not_passed)
    return "passed", None


def _as_text(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _write_result(project: Project, result: dict, combined_log: str) -> None:
    state_dir = project.root / ".veriforge"
    logs_dir = project.root / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "verilator.log").write_text(combined_log, encoding="utf-8")
    (state_dir / "run.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_verilator(project: Project) -> tuple[int, dict]:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    executable = shutil.which("verilator")
    if executable is None:
        result = {
            "status": "blocked",
            "simulator": "verilator",
            "started_at": started,
            "return_code": None,
            "tests": [],
            "waveform": None,
            "message": "Verilator was not found on PATH.",
        }
        _write_result(project, result, "VeriForge: Verilator was not found on PATH.\n")
        return 2, result

    build_dir = project.root / ".veriforge" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    if project.waveform_file and project.waveform_file.exists():
        project.waveform_file.unlink()
    command = [
        executable,
        "--binary",
        "--timing",
        "--assert",
        "--coverage",
        "--top-module",
        project.top_module,
        "--Mdir",
        str(build_dir),
    ]
    if project.waveform_file:
        command.append("--trace")
    command.extend(str(source) for source in project.sources)

    timeout = project.timeout_seconds
    output_chunks: list[str] = ["$ " + " ".join(command) + "\n"]
    compile_code: int | None = None
    run_code: int | None = None
    message: str | None = None
    started_clock = time.monotonic()
    try:
        compiled = subprocess.run(
            command,
            cwd=project.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        compile_code = compiled.returncode
        output_chunks.append(_as_text(compiled.stdout))
    except subprocess.TimeoutExpired as exc:
        compile_code = 124
        message = f"Verilator build exceeded timeout of {timeout} seconds."
        output_chunks.append(_as_text(exc.stdout) + "\n" + message + "\n")
    except OSError as exc:
        compile_code = 127
        message = f"Could not start Verilator: {exc}"
        output_chunks.append(message + "\n")

    if compile_code == 0:
        executable_path = build_dir / f"V{project.top_module}"
        if os.name == "nt":
            executable_path = executable_path.with_suffix(".exe")
        if not executable_path.is_file():
            run_code = 127
            message = f"Verilator build succeeded but output was not found: {executable_path}"
            output_chunks.append(message + "\n")
        else:
            output_chunks.append("\n$ " + str(executable_path) + "\n")
            try:
                ran = subprocess.run(
                    [str(executable_path)],
                    cwd=project.root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
                run_code = ran.returncode
                output_chunks.append(_as_text(ran.stdout))
            except subprocess.TimeoutExpired as exc:
                run_code = 124
                message = f"Simulation exceeded timeout of {timeout} seconds."
                output_chunks.append(_as_text(exc.stdout) + "\n" + message + "\n")
            except OSError as exc:
                run_code = 127
                message = f"Could not start the compiled testbench: {exc}"
                output_chunks.append(message + "\n")

    log = "".join(output_chunks)
    test_results = parse_test_markers(log)
    status, evaluation_message = evaluate_run(compile_code, run_code, test_results)
    if message is None:
        message = evaluation_message
    waveform = (
        project.waveform_file.relative_to(project.root).as_posix()
        if project.waveform_file and project.waveform_file.is_file()
        else None
    )
    result = {
        "status": status,
        "simulator": "verilator",
        "started_at": started,
        "duration_seconds": round(time.monotonic() - started_clock, 3),
        "compile_return_code": compile_code,
        "return_code": run_code if compile_code == 0 else compile_code,
        "tests": test_results,
        "waveform": waveform,
        "message": message,
    }
    _write_result(project, result, log)
    return (0 if status == "passed" else 1), result
