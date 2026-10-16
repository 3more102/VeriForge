# VeriForge project formats

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
