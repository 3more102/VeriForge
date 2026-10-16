# VeriForge v0.1 input formats

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
