# VeriForge project formats

## Simulator selection

Set `[simulator].name` to `verilator`, `icarus`, `questa`, `vcs`, or `xcelium`.
Verilator and Icarus use separate compile and run commands. Questa uses
`vlib`/`vlog`/`vsim`, VCS builds a `simv` executable, and Xcelium compiles and
runs through one `xrun` invocation. All adapters normalize return status,
`VFTEST` markers, waveform paths, logs, and run-history records. Questa, VCS,
and Xcelium require a local licensed installation; VeriForge's unit tests mock
their executables and do not replace testing against the installed vendor
tools.

## Source index configuration

An optional `[index]` table in `veriforge.toml` controls HDL indexing:

```toml
[index]
backend = "auto" # auto, scanner, or slang
slang_command = "slang"
standard = "1800-2017"
include_dirs = ["include"]
defines = ["SIMULATION=1"]
```

`auto` uses the Slang CLI when found and otherwise writes a scanner-only source
inventory. `slang` requires the executable and records syntax errors. Slang's
parse-only CST index reports declared design units without claiming semantic
elaboration. The source annotation scanner remains responsible for explicit
requirement-to-evidence links.

The generated `.veriforge/source-index.json` schema version 1 contains the
selected backend, parser/version metadata when Slang is active, and one record
per configured source with its relative path, language, SHA-256 digest, line
count, design units, parse status, and diagnostic when applicable.

## `requirements.csv`

Required columns:

| Column | Meaning |
|---|---|
| `id` | Stable requirement key referenced by source annotations. |
| `description` | Human-readable behavior to verify. |
| `priority` | Free-form priority label shown in reports. |
| `area` | Requirement grouping label. |
| `required_evidence` | Semicolon-separated subset of `test`, `assertion`, `coverage`. |

IDs must be unique and use letters, digits, underscores, or hyphens. A
requirement may have an empty `required_evidence` value when it is informational
only.

## Source annotations

```systemverilog
// vf:asserts REQ-A,REQ-B
assert property (p_a);

// vf:covers REQ-A id=condition_a
cover property (p_a);

// vf:test REQ-A test_a
```

Assertion and coverage directives apply to the next matching SystemVerilog
statement within eleven lines. Test directives associate the listed requirement
with a test ID. A test is complete only after a matching runtime marker is
recorded.

## Runtime markers

The configured testbench prints one line per test:

```text
VFTEST: test_a PASS
VFTEST: test_b FAIL expected 1, got 0
```

IDs are matched exactly and status is case-insensitive. Repeated markers for a
test use the last observed status.

## `coverage.csv`

Required columns are `requirement_id`, `coverage_id`, `hits`, and `bins`.
Coverage IDs must match source annotations. `hits` is constrained to the range
0 through `bins`; `bins` must be positive. Each requirement/coverage ID pair
must occur once.

## UCIS XML import

Run `veriforge coverage import result.xml [--project DIR] [--map FILE]` to
import an Accellera UCIS XML export. UCIS point names and aliases that exactly
match a source `vf:covers id=...` map automatically. Other names can be mapped
with a CSV containing these columns:

| Column | Meaning |
|---|---|
| `ucis_path` | Exact point path recorded in `.veriforge/ucis-import.json`. |
| `requirement_id` | Existing requirement ID. |
| `coverage_id` | Existing `vf:covers id=...` for that requirement. |

The importer applies a point's `at_least` threshold and excludes ignore and
illegal bins from closure. It writes normalized data to
`.veriforge/coverage-ucis.csv`, leaving the user-maintained `coverage.csv`
untouched. If both files contain the same requirement and coverage ID, the
`coverage.csv` measurement takes precedence. Analysis includes the UCIS source
file, source line, point path, and observed hit total in report JSON. The full
import record also retains the XML SHA-256, bin names, mapping decisions, and
unmatched points. Import the simulator's UCIS XML export; proprietary database
formats are not read directly.

Normalized UCIS rows use the same required columns as `coverage.csv` and these
optional provenance columns: `source_file`, `source_line`, `ucis_path`,
`observed_hits`, and `bin_names`.

## HTML report

`veriforge analyze` writes a self-contained `reports/veriforge-report.html`
alongside Markdown and JSON reports. It can be opened from disk without a
server or external assets. The requirement table and evidence details share a
text filter; measured coverage shows source provenance when available.

## `regression.toml`

Use one `[[case]]` table per simulator run:

```toml
[regression]
name = "ready_valid_seed_sweep"

[[case]]
name = "seed_1"
seed = 1
arguments = ["+mode=stress"]
```

Case names must be unique. Seeds are optional integers from 0 through
4,294,967,295 and are passed to the testbench as `+veriforge_seed=<N>`.
Additional `arguments` are forwarded to the simulator runtime as individual
arguments, without shell interpretation. A testbench must read the seed
plusarg if stimulus should change between seed cases.
