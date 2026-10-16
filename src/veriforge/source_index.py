"""Build a deterministic inventory of configured HDL sources.

When the optional slang executable is available, the index records syntax-tree
design units and parser diagnostics. The built-in scanner remains available so
the rest of VeriForge can run without an external parser installation.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .project import Project


_DESIGN_UNIT_KINDS = {
    "ModuleDeclaration": "module",
    "InterfaceDeclaration": "interface",
    "ProgramDeclaration": "program",
    "PackageDeclaration": "package",
    "ClassDeclaration": "class",
}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _token_text(value) -> str | None:
    if isinstance(value, dict):
        text = value.get("text")
        return text if isinstance(text, str) else None
    return value if isinstance(value, str) else None


def _design_units(cst: dict) -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in _walk(cst):
        kind = node.get("kind")
        unit_type = _DESIGN_UNIT_KINDS.get(kind)
        if unit_type is None:
            continue
        token = None
        if kind in {"ModuleDeclaration", "InterfaceDeclaration", "ProgramDeclaration"}:
            header = node.get("header")
            if isinstance(header, dict):
                token = header.get("name")
        else:
            token = node.get("name")
        name = _token_text(token)
        if name:
            key = (unit_type, name)
            if key not in seen:
                seen.add(key)
                units.append({"kind": unit_type, "name": name})
    return sorted(units, key=lambda item: (item["kind"], item["name"]))


def _source_entry(project: Project, source: Path) -> dict:
    try:
        relative = source.relative_to(project.root).as_posix()
    except ValueError:
        relative = source.as_posix()
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "path": relative,
            "language": "systemverilog" if source.suffix.lower() == ".sv" else "verilog",
            "sha256": None,
            "bytes": None,
            "lines": None,
            "status": "unreadable",
            "design_units": [],
            "diagnostic": str(exc),
        }
    return {
        "path": relative,
        "language": "systemverilog" if source.suffix.lower() == ".sv" else "verilog",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "lines": len(text.splitlines()),
        "status": "indexed",
        "design_units": [],
    }


def _slang_version(executable: str, project: Project) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            cwd=project.root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (result.stdout or result.stderr).strip()
    return value.splitlines()[0] if value else None


def _run_slang(project: Project, source: Path, executable: str) -> tuple[list[dict], str | None, int]:
    config = project.source_index
    command = [executable, "--parse-only", "--std", config.standard, "--cst-json", "-"]
    for include_dir in config.include_dirs:
        command.extend(["--include-directory", str(include_dir)])
    for definition in config.defines:
        command.extend(["--define-macro", definition])
    command.append(str(source))
    try:
        result = subprocess.run(
            command,
            cwd=project.root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return [], "Slang parsing timed out after 120 seconds", 124
    except OSError as exc:
        return [], f"Could not run Slang: {exc}", 127

    diagnostic = (result.stderr or "").strip()
    try:
        cst = json.loads(result.stdout)
    except json.JSONDecodeError:
        cst = None
    if not isinstance(cst, dict):
        message = diagnostic or "Slang did not return valid CST JSON"
        return [], message, result.returncode or 1
    units = _design_units(cst)
    if result.returncode:
        return units, diagnostic or f"Slang exited with status {result.returncode}", result.returncode
    return units, diagnostic or None, 0


def build_source_index(project: Project, backend: str | None = None) -> dict:
    """Index configured sources and optionally parse them with Slang."""
    requested = (backend or project.source_index.backend).strip().lower()
    if requested not in {"auto", "scanner", "slang"}:
        raise ValueError("backend must be 'auto', 'scanner', or 'slang'")

    executable = (
        None if requested == "scanner"
        else shutil.which(project.source_index.slang_command)
    )
    selected = requested
    warnings: list[str] = []
    if requested == "auto":
        selected = "slang" if executable else "scanner"
    if selected == "slang" and executable is None:
        raise FileNotFoundError(
            f"Slang executable {project.source_index.slang_command!r} was not found on PATH"
        )

    entries = [_source_entry(project, source) for source in project.sources]
    for entry in entries:
        if entry["status"] == "unreadable":
            warnings.append(f"Could not index {entry['path']}: {entry['diagnostic']}")
            continue
        if selected != "slang":
            continue
        source = project.root / entry["path"]
        # Sources may be intentionally referenced outside the project root.
        if not source.exists():
            source = next(
                candidate for candidate in project.sources
                if candidate.as_posix() == entry["path"]
            )
        units, diagnostic, status = _run_slang(project, source, executable)
        entry["design_units"] = units
        entry["status"] = "parsed" if status == 0 else "parse_error"
        if diagnostic:
            entry["diagnostic"] = diagnostic
            if status != 0:
                warnings.append(f"Slang could not parse {entry['path']}: {diagnostic}")

    parser = None
    if selected == "slang":
        parser = {
            "name": "slang",
            "version": _slang_version(executable, project),
            "standard": project.source_index.standard,
        }
    return {
        "schema_version": 1,
        "project": project.name,
        "backend": selected,
        "parser": parser,
        "sources": entries,
        "warnings": warnings,
    }
