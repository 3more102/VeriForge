# VeriForge

**VeriForge is a CLI-first verification gap explorer for SystemVerilog.** It
connects requirement IDs to testbench checks, assertions, coverage points, and
simulation outcomes, then gives each uncovered requirement a concrete next
action.

The v0.1 vertical slice supports Verilator and Icarus Verilog, plus a
simulator-neutral CSV coverage import. The v0.2 release adds optional adapters
for Questa, VCS, and Xcelium. Analysis is deterministic and local; the run
ledger is a small project-local SQLite database. No AI service or external
Python package is required at runtime.

## Quick start

```bash
python -m pip install -e .
python -m veriforge init demo
cd demo
python -m veriforge analyze
python -m veriforge run
python -m veriforge regress
python -m veriforge history
python -m veriforge analyze
```

`veriforge init` creates a small ready/valid buffer example. The first analysis
shows that the source contains linked assertions, tests, and coverpoints, while
test execution and coverage measurements are still pending. With the selected
simulator installed, `veriforge run` builds the testbench, captures `VFTEST`
markers, and saves the log and run record. A second analysis updates the
requirement status.

## Commands

| Command | Action |
|---|---|
| `veriforge init [directory]` | Create an example project; refuses to overwrite a non-empty directory. |
| `veriforge add rtl|tb <file>... [--project DIR]` | Copy HDL files into the project and add them to the configured source list. |
| `veriforge index [project] [--backend auto|scanner|slang]` | Inventory configured sources and, when available, parse them with Slang. |
| `veriforge analyze [project]` | Trace requirements to evidence and write Markdown and JSON reports. |
| `veriforge configure --simulator <name> [project]` | Select `verilator`, `icarus`, `questa`, `vcs`, or `xcelium`. |
| `veriforge run [project]` | Compile and run the configured testbench; save logs, waveform, and history. |
| `veriforge regress [project]` | Run every named case in `regression.toml` and write an aggregate result. |
| `veriforge history [project] [--limit N]` | Show recent passed, failed, and blocked runs. |

For example, add project-owned sources with `veriforge add rtl ../rtl/adapter.sv`
and `veriforge add tb ../tb/adapter_tb.sv`. Select an adapter with
`veriforge configure --simulator icarus` or
`veriforge configure --simulator verilator`. The selected tool must be installed
on `PATH`. Questa requires `vlib`, `vlog`, and `vsim`; VCS requires `vcs`; and
Xcelium requires `xrun`. If a required tool is missing, the run is marked
blocked and retained in history. Commercial simulators also require a valid
license and their setup environment.
Edit `top_module` in `veriforge.toml` to select the testbench top.
The default run enables tracing and produces `waveform.vcd` when the testbench
calls `$dumpfile` and `$dumpvars`. Icarus runs use `iverilog` and `vvp`; Verilator
runs use Verilator's built-in binary flow. Both require explicit `VFTEST`
markers for a passing result.

`veriforge index` inventories configured HDL files, hashes their contents, and
records declared design units. The `auto` backend uses the optional `slang`
executable when it is on `PATH`, otherwise it records a scanner-only inventory.
Install the Slang CLI from its official release to enable CST-backed parsing.
`pyslang` provides Python bindings but VeriForge currently invokes the CLI.
`--backend slang` requires the executable; `--backend scanner` explicitly skips
parsing. Slang runs in parse-only mode, so the index does not claim type
checking or elaboration.
`analyze` refreshes the same source index and continues to use the explicit
`vf:` annotations for requirement mapping.

Projects can tune the parser in `veriforge.toml`:

```toml
[index]
backend = "auto" # auto, scanner, or slang
slang_command = "slang"
standard = "1800-2017"
include_dirs = ["include"]
defines = ["SIMULATION=1"]
```

`veriforge regress` runs the cases in `regression.toml` in order. Each case can
provide a `seed` and simulator runtime `arguments`; seeds are passed as
`+veriforge_seed=<N>`. The testbench must consume that plusarg for the seed to
affect stimulus. `veriforge init` includes an example that derives two input
payloads from the selected seed. Every case gets a separate working directory,
log, and waveform so later cases do not replace earlier evidence.

## Requirement traceability

List requirements in `requirements.csv`:

```csv
id,description,priority,area,required_evidence
REQ-CRC-001,Reject a corrupted packet,high,reliability,test;assertion;coverage
```

Add annotations immediately before the relevant assertion or coverpoint. A
`vf:test` line maps a requirement to a test ID; the testbench reports runtime
results with the matching ID. A successful process exit without a passing
`VFTEST` marker is treated as an incomplete or failed run.

```systemverilog
// vf:test REQ-CRC-001 crc_corruption_test
// ... test body ...

// vf:asserts REQ-CRC-001
assert (error_o) else $fatal(1, "corrupted packet was accepted");

// vf:covers REQ-CRC-001 id=corrupted_packet
cover property (@(posedge clk) inject_error && error_o);

$display("VFTEST: crc_corruption_test PASS");
```

Requirements can choose any combination of `test`, `assertion`, and `coverage`
in `required_evidence`. The analyzer reports missing declarations, tests that
have not run, failed tests, and coverage bins that remain uncovered.

## Coverage import

Provide measured results in `coverage.csv`:

```csv
requirement_id,coverage_id,hits,bins
REQ-CRC-001,corrupted_packet,1,1
```

`coverage_id` must match an `id=` value in a `vf:covers` annotation. `hits` is
the number of bins hit; `bins` is the number of bins in that coverpoint. VeriForge
does not claim to parse proprietary coverage databases in v0.1. Convert a
simulator or UCIS export to this normalized format, or leave the row absent so
the report marks it pending.

## Project configuration

`veriforge.toml` names the project, requirement and coverage manifests, selected
simulator, top module, source files, and timeout. Paths are relative to the
configuration file. v0.1 supports Verilator and Icarus Verilog; the requirement
manifest and coverage import are simulator-neutral. Concurrent cover properties
in the example are guarded for Verilator because Icarus does not implement that
coverage syntax.

## Generated files

- `reports/veriforge-report.md` — concise gap summary and actions.
- `reports/veriforge-report.json` — machine-readable requirement evidence.
- `.veriforge/run.json` — last normalized simulation result.
- `.veriforge/history.sqlite3` — append-only run and per-test history.
- `.veriforge/regression.json` — aggregate status and artifact paths for the last regression.
- `.veriforge/source-index.json` — source hashes and parser-backed design-unit inventory.
- `logs/<simulator>.log` — compiler and simulation output.
- `logs/<simulator>-<case>.log` — per-case regression output.
- `waveform.vcd` — traced signals when the testbench enables `$dumpvars`.
- `.veriforge/regression/<case>/waveform.vcd` — per-case regression waveform.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Python 3.11 or newer is required. Runtime code uses only the standard library.

## Scope and next steps

The source scanner recognizes explicit annotations; the optional Slang CST
backend adds syntax-aware source indexing while preserving that explicit
requirement map. The remaining roadmap is UCIS import and richer HTML
exploration. Commercial adapter commands are covered by mocked runner tests;
running them against real tools requires a locally installed, licensed
simulator. VeriForge does not implement UVM automation, formal proof, or
AI-based analysis.

## License

MIT. See [LICENSE](LICENSE).
