"""Command line interface for VeriForge."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tomllib
from pathlib import Path

from . import __version__
from .analysis import analyze, render_html, render_markdown
from .history import list_runs
from .project import ProjectError, load_project
from .regression import run_regression
from .runner import run_simulation
from .scanner import scan_sources
from .source_index import build_source_index
from .ucis import import_ucis_file


_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def _example_files(name: str) -> dict[str, str]:
    project_name = name.strip() or "veriforge_demo"
    return {
        "veriforge.toml": f'''[project]
name = "{project_name}"
requirements = "requirements.csv"
coverage_file = "coverage.csv"
regression_file = "regression.toml"

[simulator]
name = "verilator"
top_module = "tb_top"
sources = ["rtl/rv_buffer.sv", "tb/tb_top.sv"]

[run]
timeout_seconds = 60
trace = true
waveform = "waveform.vcd"

[index]
backend = "auto"
standard = "1800-2017"
''',
        "requirements.csv": '''id,description,priority,area,required_evidence
REQ-RV-001,Retain output data while downstream applies backpressure,high,ready-valid,test;assertion;coverage
REQ-RV-002,Accept a new beat on the same cycle the old beat is consumed,medium,ready-valid,test;assertion;coverage
''',
        "coverage.csv": "requirement_id,coverage_id,hits,bins\n",
        "regression.toml": '''[regression]
name = "ready_valid_seed_sweep"

[[case]]
name = "seed_1"
seed = 1

[[case]]
name = "seed_2"
seed = 2
''',
        "rtl/rv_buffer.sv": '''module rv_buffer (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       valid_i,
    output logic       ready_i,
    input  logic [7:0] data_i,
    output logic       valid_o,
    input  logic       ready_o,
    output logic [7:0] data_o
);
    logic       full_q;
    logic [7:0] data_q;

    assign ready_i = !full_q || ready_o;
    assign valid_o = full_q;
    assign data_o  = data_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            full_q <= 1'b0;
            data_q <= '0;
        end else if (ready_i) begin
            full_q <= valid_i;
            if (valid_i)
                data_q <= data_i;
        end
    end
endmodule
''',
        "tb/tb_top.sv": '''module tb_top;
    logic clk = 1'b0;
    logic rst_n = 1'b0;
    logic valid_i = 1'b0;
    logic ready_i;
    logic [7:0] data_i = '0;
    logic valid_o;
    logic ready_o = 1'b0;
    logic [7:0] data_o;
    logic [31:0] seed;
    logic [7:0] first_payload;
    logic [7:0] replacement_payload;

    always #5 clk = ~clk;

    rv_buffer dut (.*);

`ifdef VERILATOR
    // vf:covers REQ-RV-001 id=blocked_valid
    cover property (@(posedge clk) valid_o && !ready_o);

    // vf:covers REQ-RV-002 id=simultaneous_consume_refill
    cover property (@(posedge clk) valid_o && ready_o && valid_i);
`endif

    initial begin
        seed = 1;
        if (!$value$plusargs("veriforge_seed=%d", seed)) seed = 1;
        first_payload = (seed[7:0] * 8'd37) + 8'd11;
        replacement_payload = (seed[7:0] * 8'd73) + 8'd29;
        $dumpfile("waveform.vcd");
        $dumpvars(0, tb_top);

        repeat (2) @(posedge clk);
        #1 rst_n = 1'b1;

        // vf:test REQ-RV-001,REQ-RV-002 rv_buffer_backpressure_and_refill
        valid_i = 1'b1;
        data_i = first_payload;
        @(posedge clk);
        #1 valid_i = 1'b0;
        // vf:asserts REQ-RV-001
        assert (valid_o && data_o == first_payload)
            else $fatal(1, "first beat was not captured");

        repeat (3) begin
            @(posedge clk);
            #1;
            // vf:asserts REQ-RV-001
            assert (valid_o && data_o == first_payload)
                else $fatal(1, "data changed while backpressured");
        end

        ready_o = 1'b1;
        valid_i = 1'b1;
        data_i = replacement_payload;
        @(posedge clk);
        #1;
        // vf:asserts REQ-RV-002
        assert (valid_o && data_o == replacement_payload)
            else $fatal(1, "replacement beat was not accepted");

        valid_i = 1'b0;
        @(posedge clk);
        #1;
        // vf:asserts REQ-RV-002
        assert (!valid_o) else $fatal(1, "buffer did not drain");

        $display("VFTEST: rv_buffer_backpressure_and_refill PASS seed=%0d", seed);
        $finish;
    end
endmodule
''',
    }


def _init_project(path: Path) -> Path:
    target = path.expanduser().resolve()
    files = _example_files(_NAME.sub("_", target.name).strip("_") or "veriforge_demo")
    if target.exists() and any(target.iterdir()):
        raise ProjectError(f"Refusing to initialize a non-empty directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    for relative, contents in files.items():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents, encoding="utf-8")
    return target


def _command_init(args: argparse.Namespace) -> int:
    try:
        target = _init_project(Path(args.path))
    except (OSError, ProjectError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    print(f"Initialized VeriForge example project at {target}")
    print("Next: python -m pip install -e . && python -m veriforge analyze")
    return 0


def _command_analyze(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.path)
        result = analyze(project, args.backend)
        reports = project.root / "reports"
        reports.mkdir(exist_ok=True)
        index_path = project.root / ".veriforge" / "source-index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(result["source_index"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (reports / "veriforge-report.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (reports / "veriforge-report.md").write_text(
            render_markdown(result), encoding="utf-8"
        )
        (reports / "veriforge-report.html").write_text(
            render_html(result), encoding="utf-8"
        )
    except (OSError, ProjectError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    summary = result["summary"]
    total = result["analysis"]["total"]
    print(
        f"{project.name}: {summary['complete']}/{total} requirements complete; "
        f"{summary['gap']} gaps, {summary['pending']} pending, {summary['failing']} failing"
    )
    print(f"Source index: {result['source_index']['backend']} ({len(result['source_index']['sources'])} sources)")
    print(f"Reports: {project.root / 'reports' / 'veriforge-report.md'}")
    print(f"HTML: {project.root / 'reports' / 'veriforge-report.html'}")
    index_failed = any(
        item["status"] in {"parse_error", "unreadable"}
        for item in result["source_index"]["sources"]
    )
    return 1 if summary["failing"] or index_failed else 0


def _command_index(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.path)
        source_index = build_source_index(project, args.backend)
        index_path = project.root / ".veriforge" / "source-index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(source_index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ProjectError, ValueError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    source_count = len(source_index["sources"])
    unit_count = sum(len(item["design_units"]) for item in source_index["sources"])
    failed = sum(item["status"] in {"parse_error", "unreadable"} for item in source_index["sources"])
    print(
        f"Indexed {source_count} source file(s) with {source_index['backend']}; "
        f"found {unit_count} design unit(s), {failed} failed."
    )
    print(f"Index: {project.root / '.veriforge' / 'source-index.json'}")
    for warning in source_index["warnings"]:
        print(f"Warning: {warning}")
    return 1 if failed else 0


def _command_coverage_import(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
        static = scan_sources(project)
        result = import_ucis_file(project, args.xml, static, args.map)
    except (OSError, ProjectError, ValueError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    print(
        f"Imported {result['mapped_count']}/{result['point_count']} UCIS point(s) "
        f"into {result['row_count']} normalized coverage row(s)."
    )
    print(f"Coverage: {result['coverage_path']}")
    print(f"Provenance: {result['metadata_path']}")
    for item in result["unmatched"]:
        print(f"Warning: Unmapped {item['ucis_path']}: {item['reason']}")
    return 1 if result["unmatched"] else 0


def _add_sources(project, kind: str, source_paths: list[str]) -> list[Path]:
    destination_dir = project.root / ("rtl" if kind == "rtl" else "tb")
    candidates: list[tuple[Path, Path]] = []
    for raw_path in source_paths:
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise ProjectError(f"Source file not found: {raw_path}")
        if source.suffix.lower() not in {".sv", ".v"}:
            raise ProjectError(f"Unsupported source extension {source.suffix!r}; use .sv or .v")
        destination = destination_dir / source.name
        if destination.exists():
            raise ProjectError(f"Project file already exists; refusing to overwrite: {destination}")
        if any(destination == previous for _, previous in candidates):
            raise ProjectError(f"More than one input has the same destination name: {source.name}")
        candidates.append((source, destination))

    config_path = project.root / "veriforge.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config = tomllib.loads(config_text)
    source_list = config.get("simulator", {}).get("sources")
    if not isinstance(source_list, list) or not all(isinstance(item, str) for item in source_list):
        raise ProjectError("[simulator].sources must be an array of paths")
    normalized = {Path(item).as_posix() for item in source_list}
    additions = [
        (destination.relative_to(project.root).as_posix(), source, destination)
        for source, destination in candidates
    ]
    for relative, _, _ in additions:
        if relative in normalized:
            raise ProjectError(f"Source is already listed in veriforge.toml: {relative}")
    new_source_list = [*source_list, *(relative for relative, _, _ in additions)]

    lines = config_text.splitlines()
    in_simulator = False
    source_start = source_end = None
    for index, line in enumerate(lines):
        table = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
        if table:
            in_simulator = table.group(1).strip() == "simulator"
        if in_simulator and re.match(r"^\s*sources\s*=", line):
            source_start = index
            source_end = index
            while "]" not in lines[source_end]:
                source_end += 1
                if source_end >= len(lines):
                    raise ProjectError("Could not locate the end of [simulator].sources")
            break
    if source_start is None or source_end is None:
        raise ProjectError("Could not find [simulator].sources in veriforge.toml")
    serialized = "sources = [" + ", ".join(json.dumps(item) for item in new_source_list) + "]"
    lines[source_start:source_end + 1] = [serialized]
    updated_config = "\n".join(lines) + "\n"

    copied: list[Path] = []
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
        for _, source, destination in additions:
            shutil.copy2(source, destination)
            copied.append(destination)
        temporary = config_path.with_suffix(".toml.tmp")
        temporary.write_text(updated_config, encoding="utf-8")
        os.replace(temporary, config_path)
    except OSError:
        for path in copied:
            path.unlink(missing_ok=True)
        raise
    return [destination for _, _, destination in additions]


def _command_add(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.project)
        added = _add_sources(project, args.kind, args.sources)
        refreshed = load_project(project.root)
    except (OSError, ProjectError, tomllib.TOMLDecodeError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    print(f"Added {len(added)} {args.kind.upper()} source file(s):")
    for path in added:
        print(f"- {path.relative_to(project.root).as_posix()}")
    print(f"Project now has {len(refreshed.sources)} configured source file(s).")
    return 0


def _command_run(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.path)
        code, result = run_simulation(project)
    except (OSError, ProjectError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    print(f"{result['simulator'].capitalize()} run #{result.get('run_id', '?')}: {result['status']}")
    if result.get("message"):
        print(result["message"])
    if result.get("history_error"):
        print(result["history_error"])
    print(f"Log: {project.root / 'logs' / (result['simulator'] + '.log')}")
    if result.get("waveform"):
        print(f"Waveform: {project.root / result['waveform']}")
    return code


def _command_configure(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.path)
        config_path = project.root / "veriforge.toml"
        lines = config_path.read_text(encoding="utf-8").splitlines()
        in_simulator = False
        name_line = None
        for index, line in enumerate(lines):
            table = re.match(r"^\s*\[([^]]+)\]\s*(?:#.*)?$", line)
            if table:
                in_simulator = table.group(1).strip() == "simulator"
            if in_simulator and re.match(r"^\s*name\s*=", line):
                name_line = index
                break
        if name_line is None:
            raise ProjectError("Could not find [simulator].name in veriforge.toml")
        lines[name_line] = f"name = {json.dumps(args.simulator)}"
        temporary = config_path.with_suffix(".toml.tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, config_path)
        configured = load_project(project.root)
    except (OSError, ProjectError, tomllib.TOMLDecodeError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    print(f"Configured {configured.name} to use {configured.simulator}.")
    return 0


def _command_history(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.path)
        runs = list_runs(project.root, args.limit)
    except (OSError, sqlite3.Error, ProjectError, ValueError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    if not runs:
        print(f"{project.name}: no simulation runs recorded yet.")
        return 0
    print(f"Recent runs for {project.name} (newest first):")
    for run in runs:
        tests = f"{run['passed_tests'] or 0}/{run['test_count']} tests passed"
        scenario = f" [{run['scenario']}]" if run.get("scenario") else ""
        seed = f" seed={run['seed']}" if run.get("seed") is not None else ""
        print(
            f"#{run['id']} {run['started_at']}  {run['simulator']}{scenario}{seed}  "
            f"{run['status']}  {tests}"
        )
        if run.get("message"):
            print(f"  {run['message']}")
    return 0


def _command_regress(args: argparse.Namespace) -> int:
    try:
        project = load_project(args.path)
        code, summary = run_regression(project)
    except (OSError, ProjectError) as exc:
        print(f"veriforge: {exc}", file=sys.stderr)
        return 2
    print(
        f"Regression {summary['name']}: {summary['status']} — "
        f"{summary['passed_cases']}/{summary['case_count']} cases passed"
    )
    for case in summary["cases"]:
        seed = f" seed={case['seed']}" if case["seed"] is not None else ""
        print(f"- {case['name']}{seed}: {case['status']} (run #{case['run_id']})")
        if case.get("message"):
            print(f"  {case['message']}")
    print(f"Summary: {project.root / '.veriforge' / 'regression.json'}")
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veriforge",
        description="Connect requirements to tests, assertions, coverage, and simulation results.",
    )
    parser.add_argument("--version", action="version", version=f"VeriForge {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a runnable example project")
    init_parser.add_argument("path", nargs="?", default=".", help="new or empty project directory")
    init_parser.set_defaults(func=_command_init)

    analyze_parser = subparsers.add_parser("analyze", help="find verification evidence gaps")
    analyze_parser.add_argument("path", nargs="?", default=".", help="project directory or config path")
    analyze_parser.add_argument("--backend", choices=("auto", "scanner", "slang"), help="source index backend")
    analyze_parser.set_defaults(func=_command_analyze)

    index_parser = subparsers.add_parser("index", help="parse and inventory configured HDL sources")
    index_parser.add_argument("path", nargs="?", default=".", help="project directory or config path")
    index_parser.add_argument("--backend", choices=("auto", "scanner", "slang"), help="source index backend")
    index_parser.set_defaults(func=_command_index)

    add_parser = subparsers.add_parser("add", help="copy RTL or testbench files into a project")
    add_parser.add_argument("kind", choices=("rtl", "tb"), help="destination source class")
    add_parser.add_argument("sources", nargs="+", help="one or more .sv or .v files")
    add_parser.add_argument("--project", default=".", help="target project directory")
    add_parser.set_defaults(func=_command_add)

    configure_parser = subparsers.add_parser("configure", help="select the project simulator")
    configure_parser.add_argument(
        "--simulator", required=True,
        choices=("verilator", "icarus", "questa", "vcs", "xcelium"),
    )
    configure_parser.add_argument("path", nargs="?", default=".", help="project directory or config path")
    configure_parser.set_defaults(func=_command_configure)

    run_parser = subparsers.add_parser("run", help="build and run the configured testbench")
    run_parser.add_argument("path", nargs="?", default=".", help="project directory or config path")
    run_parser.set_defaults(func=_command_run)

    history_parser = subparsers.add_parser("history", help="show recorded simulation runs")
    history_parser.add_argument("path", nargs="?", default=".", help="project directory or config path")
    history_parser.add_argument("--limit", type=int, default=20, help="maximum runs to show")
    history_parser.set_defaults(func=_command_history)

    regress_parser = subparsers.add_parser("regress", help="run named seeded regression cases")
    regress_parser.add_argument("path", nargs="?", default=".", help="project directory or config path")
    regress_parser.set_defaults(func=_command_regress)

    coverage_parser = subparsers.add_parser("coverage", help="import simulator coverage data")
    coverage_subparsers = coverage_parser.add_subparsers(dest="coverage_command", required=True)
    import_parser = coverage_subparsers.add_parser("import", help="import a UCIS XML coverage export")
    import_parser.add_argument("xml", help="UCIS XML file exported by a simulator")
    import_parser.add_argument("--project", default=".", help="target project directory")
    import_parser.add_argument("--map", help="CSV mapping with ucis_path,requirement_id,coverage_id columns")
    import_parser.set_defaults(func=_command_coverage_import)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
