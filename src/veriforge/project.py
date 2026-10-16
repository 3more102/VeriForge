"""Project configuration and manifest loading."""

from __future__ import annotations

import csv
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ProjectError(ValueError):
    """Raised when a VeriForge project is invalid or incomplete."""


@dataclass(frozen=True)
class Requirement:
    id: str
    description: str
    priority: str
    area: str
    required_evidence: tuple[str, ...]


@dataclass(frozen=True)
class SourceIndexConfig:
    backend: str
    slang_command: str
    standard: str
    include_dirs: tuple[Path, ...]
    defines: tuple[str, ...]


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    simulator: str
    top_module: str
    sources: tuple[Path, ...]
    timeout_seconds: int
    waveform_file: Path | None
    requirements: tuple[Requirement, ...]
    coverage_file: Path
    regression_file: Path
    source_index: SourceIndexConfig


_REQ_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_HDL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_EVIDENCE_TYPES = {"test", "assertion", "coverage"}
_SIMULATORS = {"verilator", "icarus", "questa", "vcs", "xcelium"}
_SLANG_STANDARDS = {
    "1364-1995", "1364-2001-noconfig", "1364-2001", "1364-2005",
    "1800-2005", "1800-2009", "1800-2012", "1800-2017", "1800-2023", "latest",
}
_REQUIREMENT_COLUMNS = {
    "id", "description", "priority", "area", "required_evidence"
}


def _read_requirements(path: Path) -> tuple[Requirement, ...]:
    if not path.is_file():
        raise ProjectError(f"Requirements manifest not found: {path}")
    requirements: list[Requirement] = []
    seen: set[str] = set()
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or ())
            missing = sorted(_REQUIREMENT_COLUMNS - columns)
            if missing:
                raise ProjectError(
                    f"{path.name} is missing columns: {', '.join(missing)}"
                )
            for row_number, row in enumerate(reader, start=2):
                req_id = (row.get("id") or "").strip()
                if not req_id or not _REQ_ID.fullmatch(req_id):
                    raise ProjectError(
                        f"{path.name}:{row_number}: invalid requirement id {req_id!r}"
                    )
                if req_id in seen:
                    raise ProjectError(
                        f"{path.name}:{row_number}: duplicate requirement id {req_id}"
                    )
                seen.add(req_id)
                description = (row.get("description") or "").strip()
                if not description:
                    raise ProjectError(
                        f"{path.name}:{row_number}: description cannot be empty"
                    )
                required = tuple(
                    item.strip().lower()
                    for item in (row.get("required_evidence") or "").split(";")
                    if item.strip()
                )
                unknown = sorted(set(required) - _EVIDENCE_TYPES)
                if unknown:
                    raise ProjectError(
                        f"{path.name}:{row_number}: unsupported evidence type(s): "
                        f"{', '.join(unknown)}"
                    )
                if len(set(required)) != len(required):
                    raise ProjectError(
                        f"{path.name}:{row_number}: required_evidence contains duplicates"
                    )
                requirements.append(
                    Requirement(
                        id=req_id,
                        description=description,
                        priority=(row.get("priority") or "medium").strip().lower(),
                        area=(row.get("area") or "general").strip(),
                        required_evidence=required,
                    )
                )
    except OSError as exc:
        raise ProjectError(f"Cannot read requirements manifest {path}: {exc}") from exc
    if not requirements:
        raise ProjectError(f"{path.name} contains no requirements")
    return tuple(requirements)


def load_project(path: str | Path) -> Project:
    """Load a project directory or its ``veriforge.toml`` file."""
    supplied = Path(path).expanduser().resolve()
    config_path = supplied / "veriforge.toml" if supplied.is_dir() else supplied
    if config_path.name != "veriforge.toml":
        raise ProjectError(f"Expected a project directory or veriforge.toml: {supplied}")
    if not config_path.is_file():
        raise ProjectError(f"Project configuration not found: {config_path}")
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectError(f"Cannot read {config_path}: {exc}") from exc

    project = config.get("project", {})
    simulator = config.get("simulator", {})
    run = config.get("run", {})
    index = config.get("index", {})
    if not all(isinstance(section, dict) for section in (project, simulator, run, index)):
        raise ProjectError("[project], [simulator], [run], and [index] must be TOML tables")
    name = str(project.get("name", "")).strip()
    sim_name = str(simulator.get("name", "verilator")).strip().lower()
    top = str(simulator.get("top_module", "")).strip()
    sources_value = simulator.get("sources", [])
    if not name:
        raise ProjectError("[project].name must be set in veriforge.toml")
    if sim_name not in _SIMULATORS:
        raise ProjectError(
            f"Simulator {sim_name!r} is not supported; choose one of: "
            + ", ".join(sorted(_SIMULATORS))
        )
    if not _HDL_IDENTIFIER.fullmatch(top):
        raise ProjectError("[simulator].top_module must be a SystemVerilog identifier")
    if not isinstance(sources_value, list) or not sources_value:
        raise ProjectError("[simulator].sources must be a non-empty array")

    root = config_path.parent
    sources = tuple((root / str(item)).resolve() for item in sources_value)
    missing_sources = [source for source in sources if not source.is_file()]
    if missing_sources:
        listing = ", ".join(str(item) for item in missing_sources)
        raise ProjectError(f"Configured source file(s) not found: {listing}")

    manifest_name = str(project.get("requirements", "requirements.csv"))
    coverage_name = str(project.get("coverage_file", "coverage.csv"))
    regression_name = str(project.get("regression_file", "regression.toml"))
    timeout = run.get("timeout_seconds", 60)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise ProjectError("[run].timeout_seconds must be a positive integer")
    trace = run.get("trace", True)
    if not isinstance(trace, bool):
        raise ProjectError("[run].trace must be true or false")
    waveform_name = run.get("waveform", "waveform.vcd")
    if not isinstance(waveform_name, str):
        raise ProjectError("[run].waveform must be a relative file path")
    waveform_file = None
    if trace and waveform_name.strip():
        waveform_file = (root / waveform_name).resolve()
        try:
            waveform_file.relative_to(root)
        except ValueError as exc:
            raise ProjectError("[run].waveform must stay inside the project directory") from exc

    backend = str(index.get("backend", "auto")).strip().lower()
    if backend not in {"auto", "scanner", "slang"}:
        raise ProjectError("[index].backend must be 'auto', 'scanner', or 'slang'")
    slang_command = index.get("slang_command", "slang")
    if not isinstance(slang_command, str) or not slang_command.strip():
        raise ProjectError("[index].slang_command must be a non-empty executable name")
    standard = str(index.get("standard", "1800-2017")).strip()
    if standard not in _SLANG_STANDARDS:
        raise ProjectError(
            "[index].standard must be one of: " + ", ".join(sorted(_SLANG_STANDARDS))
        )
    include_values = index.get("include_dirs", [])
    if not isinstance(include_values, list) or not all(
        isinstance(item, str) and item.strip() for item in include_values
    ):
        raise ProjectError("[index].include_dirs must be an array of non-empty paths")
    define_values = index.get("defines", [])
    if not isinstance(define_values, list) or not all(
        isinstance(item, str) and item.strip() for item in define_values
    ):
        raise ProjectError("[index].defines must be an array of non-empty macro definitions")
    source_index = SourceIndexConfig(
        backend=backend,
        slang_command=slang_command.strip(),
        standard=standard,
        include_dirs=tuple((root / item).resolve() for item in include_values),
        defines=tuple(item.strip() for item in define_values),
    )

    return Project(
        root=root,
        name=name,
        simulator=sim_name,
        top_module=top,
        sources=sources,
        timeout_seconds=timeout,
        waveform_file=waveform_file,
        requirements=_read_requirements((root / manifest_name).resolve()),
        coverage_file=(root / coverage_name).resolve(),
        regression_file=(root / regression_name).resolve(),
        source_index=source_index,
    )
