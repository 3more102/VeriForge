"""Simulator adapters for reproducible, marker-checked HDL runs."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .history import record_run
from .project import Project, ProjectError


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
        return "failed", "HDL compilation failed."
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
    scenario = result.get("scenario")
    suffix = ""
    if scenario:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", scenario)
        suffix = f"-{safe_name or 'case'}"
    log_name = f"{result['simulator']}{suffix}.log"
    log_path = logs_dir / log_name
    log_path.write_text(combined_log, encoding="utf-8")
    try:
        result["run_id"] = record_run(
            project.root, result, log_path.relative_to(project.root).as_posix()
        )
    except (OSError, sqlite3.Error) as exc:
        # Keep the simulation result useful even if local history storage is unavailable.
        result["history_error"] = f"Could not persist run history: {exc}"
    (state_dir / "run.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _blocked(
    project: Project,
    simulator: str,
    message: str,
    scenario: str | None,
    seed: int | None,
    runtime_args: list[str],
) -> tuple[int, dict]:
    result = {
        "status": "blocked",
        "simulator": simulator,
        "top_module": project.top_module,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": 0.0,
        "compile_return_code": None,
        "return_code": None,
        "tests": [],
        "waveform": None,
        "message": message,
        "execution_mode": "combined" if simulator == "xcelium" else "compile-run",
        "scenario": scenario,
        "seed": seed,
        "arguments": runtime_args,
    }
    _write_result(project, result, f"VeriForge: {message}\n")
    return 2, result


def _run_adapter(
    project: Project,
    simulator: str,
    compile_command: list[str],
    run_command: list[str],
    expected_output: Path | None,
    required_tools: list[str],
    runtime_args: list[str],
    scenario: str | None,
    seed: int | None,
    execution_dir: Path | None,
    prepare_commands: list[list[str]] | None = None,
    combined_compile_and_run: bool = False,
    output_is_directory: bool = False,
) -> tuple[int, dict]:
    if project.waveform_file and project.waveform_file.exists():
        project.waveform_file.unlink()
    missing_tools = [tool for tool in required_tools if shutil.which(tool) is None]
    if missing_tools:
        names = ", ".join(missing_tools)
        return _blocked(
            project,
            simulator,
            f"Required tool(s) not found on PATH: {names}.",
            scenario,
            seed,
            runtime_args,
        )

    if combined_compile_and_run:
        compile_command = [*compile_command, *runtime_args]
    else:
        run_command = [*run_command, *runtime_args]
    run_cwd = execution_dir or project.root
    run_cwd.mkdir(parents=True, exist_ok=True)
    timeout = project.timeout_seconds
    output_chunks: list[str] = []
    compile_code: int | None = None
    run_code: int | None = None
    message: str | None = None
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started_clock = time.monotonic()

    for prepare_command in prepare_commands or []:
        output_chunks.append("$ " + " ".join(prepare_command) + "\n")
        try:
            prepared = subprocess.run(
                prepare_command,
                cwd=run_cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            output_chunks.append(_as_text(prepared.stdout))
            if prepared.returncode != 0:
                compile_code = prepared.returncode
                message = f"{simulator} preparation failed."
                break
        except subprocess.TimeoutExpired as exc:
            compile_code = 124
            message = f"{simulator} setup exceeded timeout of {timeout} seconds."
            output_chunks.append(_as_text(exc.stdout) + "\n" + message + "\n")
            break
        except OSError as exc:
            compile_code = 127
            message = f"Could not start {simulator} setup command: {exc}"
            output_chunks.append(message + "\n")
            break

    if compile_code is None:
        output_chunks.append("\n$ " + " ".join(compile_command) + "\n")
        try:
            compiled = subprocess.run(
                compile_command,
                cwd=run_cwd,
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
            message = f"{simulator} build exceeded timeout of {timeout} seconds."
            output_chunks.append(_as_text(exc.stdout) + "\n" + message + "\n")
        except OSError as exc:
            compile_code = 127
            message = f"Could not start {simulator} compiler: {exc}"
            output_chunks.append(message + "\n")

    if combined_compile_and_run:
        run_code = compile_code
    elif compile_code == 0:
        output_found = (
            expected_output is None
            or (expected_output.is_dir() if output_is_directory else expected_output.is_file())
        )
        if not output_found:
            run_code = 127
            message = f"{simulator} build succeeded but output was not found: {expected_output}"
            output_chunks.append(message + "\n")
        else:
            output_chunks.append("\n$ " + " ".join(run_command) + "\n")
            try:
                ran = subprocess.run(
                    run_command,
                    cwd=run_cwd,
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
                message = f"Could not start the {simulator} simulation: {exc}"
                output_chunks.append(message + "\n")

    log = "".join(output_chunks)
    tests = parse_test_markers(log)
    status, evaluation_message = evaluate_run(compile_code, run_code, tests)
    if message is None:
        message = evaluation_message
    waveform = (
        project.waveform_file.relative_to(project.root).as_posix()
        if project.waveform_file and project.waveform_file.is_file()
        else None
    )
    result = {
        "status": status,
        "simulator": simulator,
        "top_module": project.top_module,
        "started_at": started_at,
        "duration_seconds": round(time.monotonic() - started_clock, 3),
        "compile_return_code": compile_code,
        "return_code": run_code if compile_code == 0 else compile_code,
        "tests": tests,
        "waveform": waveform,
        "message": message,
        "execution_mode": "combined" if combined_compile_and_run else "compile-run",
        "scenario": scenario,
        "seed": seed,
        "arguments": runtime_args,
    }
    _write_result(project, result, log)
    return (0 if status == "passed" else 1), result


def run_verilator(
    project: Project,
    *,
    runtime_args: list[str] | tuple[str, ...] = (),
    scenario: str | None = None,
    seed: int | None = None,
    execution_dir: Path | None = None,
) -> tuple[int, dict]:
    """Build and run a Verilator binary with assertions and optional tracing."""
    build_dir = project.root / ".veriforge" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("verilator") or "verilator"
    output = build_dir / f"V{project.top_module}"
    if os.name == "nt":
        output = output.with_suffix(".exe")
    compile_command = [
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
        compile_command.append("--trace")
    compile_command.extend(str(source) for source in project.sources)
    return _run_adapter(
        project,
        "verilator",
        compile_command,
        [str(output)],
        output,
        ["verilator"],
        list(runtime_args),
        scenario,
        seed,
        execution_dir,
    )


def run_icarus(
    project: Project,
    *,
    runtime_args: list[str] | tuple[str, ...] = (),
    scenario: str | None = None,
    seed: int | None = None,
    execution_dir: Path | None = None,
) -> tuple[int, dict]:
    """Compile and run an Icarus Verilog testbench with VCD support."""
    build_dir = project.root / ".veriforge" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("iverilog") or "iverilog"
    runtime = shutil.which("vvp") or "vvp"
    output = build_dir / "simulation.vvp"
    compile_command = [
        compiler,
        "-g2012",
        "-gassertions",
        "-s",
        project.top_module,
        "-o",
        str(output),
        *(str(source) for source in project.sources),
    ]
    return _run_adapter(
        project,
        "icarus",
        compile_command,
        [runtime, str(output)],
        output,
        ["iverilog", "vvp"],
        list(runtime_args),
        scenario,
        seed,
        execution_dir,
    )


def run_questa(
    project: Project,
    *,
    runtime_args: list[str] | tuple[str, ...] = (),
    scenario: str | None = None,
    seed: int | None = None,
    execution_dir: Path | None = None,
) -> tuple[int, dict]:
    """Compile with Questa vlog and run the selected top in batch mode."""
    run_dir = execution_dir or project.root
    library = run_dir / "work"
    prepare_commands = [] if (library / "info").is_file() else [["vlib", "work"]]
    compiler = shutil.which("vlog") or "vlog"
    simulator = shutil.which("vsim") or "vsim"
    compile_command = [
        compiler, "-sv", "-work", "work", *(str(source) for source in project.sources),
    ]
    run_command = [
        simulator, "-c", "-do", "run -all; quit -f", f"work.{project.top_module}",
    ]
    return _run_adapter(
        project,
        "questa",
        compile_command,
        run_command,
        library,
        ["vlib", "vlog", "vsim"],
        list(runtime_args),
        scenario,
        seed,
        execution_dir,
        prepare_commands=prepare_commands,
        output_is_directory=True,
    )


def run_vcs(
    project: Project,
    *,
    runtime_args: list[str] | tuple[str, ...] = (),
    scenario: str | None = None,
    seed: int | None = None,
    execution_dir: Path | None = None,
) -> tuple[int, dict]:
    """Build a VCS simulation executable and run it with the case arguments."""
    build_dir = project.root / ".veriforge" / "build" / "vcs"
    build_dir.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which("vcs") or "vcs"
    output = build_dir / "simv"
    compile_command = [
        compiler,
        "-full64",
        "-sverilog",
        "-top",
        project.top_module,
        "-o",
        str(output),
        *(str(source) for source in project.sources),
    ]
    return _run_adapter(
        project,
        "vcs",
        compile_command,
        [str(output)],
        output,
        ["vcs"],
        list(runtime_args),
        scenario,
        seed,
        execution_dir,
    )


def run_xcelium(
    project: Project,
    *,
    runtime_args: list[str] | tuple[str, ...] = (),
    scenario: str | None = None,
    seed: int | None = None,
    execution_dir: Path | None = None,
) -> tuple[int, dict]:
    """Compile and run Xcelium in one batch invocation."""
    run_dir = execution_dir or project.root
    library_dir = run_dir / ".veriforge" / "xcelium"
    library_dir.parent.mkdir(parents=True, exist_ok=True)
    executable = shutil.which("xrun") or "xrun"
    command = [
        executable,
        "-sv",
        "-top",
        project.top_module,
        "-xmlibdirname",
        str(library_dir),
        *(str(source) for source in project.sources),
    ]
    return _run_adapter(
        project,
        "xcelium",
        command,
        [],
        None,
        ["xrun"],
        list(runtime_args),
        scenario,
        seed,
        execution_dir,
        combined_compile_and_run=True,
    )


def run_simulation(
    project: Project,
    *,
    runtime_args: list[str] | tuple[str, ...] = (),
    scenario: str | None = None,
    seed: int | None = None,
    execution_dir: Path | None = None,
) -> tuple[int, dict]:
    """Run the configured adapter."""
    adapters = {
        "verilator": run_verilator,
        "icarus": run_icarus,
        "questa": run_questa,
        "vcs": run_vcs,
        "xcelium": run_xcelium,
    }
    try:
        adapter = adapters[project.simulator]
    except KeyError as exc:
        raise ProjectError(f"Unsupported simulator: {project.simulator}") from exc
    return adapter(
        project,
        runtime_args=runtime_args,
        scenario=scenario,
        seed=seed,
        execution_dir=execution_dir,
    )
