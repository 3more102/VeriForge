# Roadmap

## v0.1 — usable vertical slice

- [x] Initialize a reproducible SystemVerilog example project.
- [x] Add RTL and testbench sources through the CLI.
- [x] Map requirements to annotated tests, assertions, and coverpoints.
- [x] Run a Verilator build and simulation, with timeout, saved logs, and VCD tracing.
- [x] Select between Verilator and Icarus Verilog adapters from the CLI.
- [x] Persist normalized run and test outcomes in local SQLite history.
- [x] Normalize pass/fail markers and imported coverage bins.
- [x] Generate deterministic Markdown and JSON gap reports.
- [x] Add input validation and unit tests.

## Next implementation steps

1. Replace annotation-only source discovery with a Slang-backed index while
   preserving the explicit requirement mapping model.
2. Add a regression manifest and aggregate results across named seeds and tests.
3. Add normalized adapters for Questa, VCS, and Xcelium execution.
4. Import UCIS coverage data and preserve source-level provenance.
5. Add a lightweight local HTML view after the report model stabilizes.

Formal analysis and AI-assisted debugging are later extensions. They are not
part of the v0.1 execution path.
