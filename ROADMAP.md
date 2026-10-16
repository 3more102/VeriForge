# Roadmap

## v0.1 — usable vertical slice

- [x] Initialize a reproducible SystemVerilog example project.
- [x] Add RTL and testbench sources through the CLI.
- [x] Map requirements to annotated tests, assertions, and coverpoints.
- [x] Run a Verilator build and simulation, with timeout, saved logs, and VCD tracing.
- [x] Select between Verilator and Icarus Verilog adapters from the CLI.
- [x] Persist normalized run and test outcomes in local SQLite history.
- [x] Run named, seeded regression cases and aggregate their results.
- [x] Normalize pass/fail markers and imported coverage bins.
- [x] Generate deterministic Markdown and JSON gap reports.
- [x] Add input validation and unit tests.

## v0.2 — parser-backed analysis and simulator breadth

- [x] Add an optional Slang CST source index while preserving explicit
  requirement annotations and scanner-only operation.
- [x] Add normalized adapters for Questa, VCS, and Xcelium execution.
- [x] Import UCIS XML coverage data, apply bin thresholds, and preserve
  source-level provenance with explicit mapping support.
- [x] Add a searchable, self-contained local HTML report with coverage provenance.

Formal analysis and AI-assisted debugging are later extensions. They are not
part of the v0.2 execution path.
