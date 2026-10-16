"""Requirement-level gap and closure analysis."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

from .project import Project, Requirement, ProjectError
from .scanner import StaticEvidence, scan_sources
from .source_index import build_source_index


_COVERAGE_COLUMNS = {"requirement_id", "coverage_id", "hits", "bins"}


def load_coverage(project: Project) -> tuple[dict[str, dict[str, dict]], list[str]]:
    records: dict[str, dict[str, dict]] = {}
    warnings: list[str] = []
    imports = [
        project.coverage_file,
        project.root / ".veriforge" / "coverage-ucis.csv",
    ]
    found_import = False
    for import_path in imports:
        if not import_path.exists():
            continue
        found_import = True
        try:
            with import_path.open(newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                missing = sorted(_COVERAGE_COLUMNS - set(reader.fieldnames or ()))
                if missing:
                    raise ProjectError(
                        f"{import_path.name} is missing columns: {', '.join(missing)}"
                    )
                seen_in_file: set[tuple[str, str]] = set()
                for row_number, row in enumerate(reader, start=2):
                    req_id = (row.get("requirement_id") or "").strip()
                    coverage_id = (row.get("coverage_id") or "").strip()
                    try:
                        hits = int((row.get("hits") or "").strip())
                        bins = int((row.get("bins") or "").strip())
                        observed_hits_text = (row.get("observed_hits") or "").strip()
                        observed_hits = int(observed_hits_text) if observed_hits_text else None
                    except ValueError as exc:
                        raise ProjectError(
                            f"{import_path.name}:{row_number}: hits, bins, and observed_hits must be integers"
                        ) from exc
                    if not req_id or not coverage_id:
                        raise ProjectError(
                            f"{import_path.name}:{row_number}: requirement_id and coverage_id are required"
                        )
                    if hits < 0 or bins < 1 or hits > bins:
                        raise ProjectError(
                            f"{import_path.name}:{row_number}: expected 0 <= hits <= bins and bins >= 1"
                        )
                    if observed_hits is not None and observed_hits < 0:
                        raise ProjectError(
                            f"{import_path.name}:{row_number}: observed_hits cannot be negative"
                        )
                    pair = (req_id, coverage_id)
                    if pair in seen_in_file:
                        raise ProjectError(
                            f"{import_path.name}:{row_number}: duplicate coverage id {coverage_id}"
                        )
                    seen_in_file.add(pair)
                    if coverage_id in records.setdefault(req_id, {}):
                        warnings.append(
                            f"Coverage import {import_path.name} duplicates {req_id}/{coverage_id}; "
                            "the earlier import was kept"
                        )
                        continue
                    bin_names = (row.get("bin_names") or "").strip()
                    if bin_names:
                        try:
                            bin_names = json.loads(bin_names)
                        except json.JSONDecodeError:
                            bin_names = [item for item in bin_names.split(";") if item]
                    else:
                        bin_names = []
                    try:
                        source_name = import_path.relative_to(project.root).as_posix()
                    except ValueError:
                        source_name = import_path.name
                    records[req_id][coverage_id] = {
                        "hits": hits,
                        "bins": bins,
                        "source": source_name,
                        "line": row_number,
                        "source_file": (row.get("source_file") or "").strip() or None,
                        "source_line": (row.get("source_line") or "").strip() or None,
                        "ucis_path": (row.get("ucis_path") or "").strip() or None,
                        "observed_hits": observed_hits,
                        "bin_names": bin_names,
                    }
        except OSError as exc:
            raise ProjectError(f"Cannot read coverage import {import_path}: {exc}") from exc

    if not found_import:
        required = any("coverage" in req.required_evidence for req in project.requirements)
        if required:
            warnings.append(f"Coverage import not found: {project.coverage_file.name}")

    metadata_path = project.root / ".veriforge" / "ucis-import.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"Cannot read UCIS import metadata {metadata_path}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ProjectError(f"UCIS import metadata must be a JSON object: {metadata_path}")
        warnings.extend(str(item) for item in metadata.get("warnings", []) if item)
    return records, warnings


def _load_run(project: Project) -> dict | None:
    path = project.root / ".veriforge" / "run.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError(f"Cannot read simulation result {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectError(f"Simulation result must be a JSON object: {path}")
    return value


def _evaluate_requirement(
    req: Requirement,
    static: StaticEvidence,
    coverage: dict[str, dict[str, dict]],
    run: dict | None,
) -> dict:
    declared = {
        "assertion": static.assertions.get(req.id, []),
        "test": static.tests.get(req.id, []),
        "coverage": static.coverpoints.get(req.id, []),
    }
    run_tests = {
        item.get("id"): item.get("status", "unknown").lower()
        for item in (run or {}).get("tests", [])
        if isinstance(item, dict) and item.get("id")
    }
    missing: list[str] = []
    pending: list[str] = []
    failures: list[str] = []
    completed: list[str] = []
    coverage_summary: list[dict] = []
    cover_ids: list[str] = []

    for evidence_type in req.required_evidence:
        if evidence_type == "assertion":
            if declared["assertion"]:
                completed.append("assertion")
            else:
                missing.append("assertion")
        elif evidence_type == "test":
            test_ids = declared["test"]
            if not test_ids:
                missing.append("test")
                continue
            if run is None:
                pending.append("test run")
                continue
            statuses = [run_tests.get(test_id) for test_id in test_ids]
            failed = [test_id for test_id, status in zip(test_ids, statuses) if status == "fail"]
            if failed:
                failures.append("failed test(s): " + ", ".join(failed))
            elif all(status == "pass" for status in statuses):
                completed.append("test")
            elif any(status == "pass" for status in statuses):
                pending.append("results for all mapped tests")
            elif run.get("status") == "failed":
                failures.append("simulation run failed")
            else:
                pending.append("test result")
        elif evidence_type == "coverage":
            cover_ids = static.coverage_ids.get(req.id, [])
            if not cover_ids:
                missing.append("coverage point")
                continue
            imported = coverage.get(req.id, {})
            absent = [coverage_id for coverage_id in cover_ids if coverage_id not in imported]
            measured = [imported[cid] for cid in cover_ids if cid in imported]
            coverage_summary = []
            for cid in cover_ids:
                if cid not in imported:
                    continue
                record = imported[cid]
                coverage_summary.append({
                    "id": cid,
                    "hits": record["hits"],
                    "bins": record["bins"],
                    "source": record.get("source"),
                    "source_file": record.get("source_file"),
                    "source_line": record.get("source_line"),
                    "ucis_path": record.get("ucis_path"),
                    "observed_hits": record.get("observed_hits"),
                    "bin_names": record.get("bin_names", []),
                })
            if absent:
                pending.append("coverage import for " + ", ".join(absent))
            if measured and any(item["hits"] < item["bins"] for item in measured):
                total_hits = sum(item["hits"] for item in measured)
                total_bins = sum(item["bins"] for item in measured)
                missing.append(f"coverage closure ({total_hits}/{total_bins} bins hit)")
            elif measured and not absent:
                completed.append("coverage")

    if failures:
        status = "failing"
    elif missing:
        status = "gap"
    elif pending:
        status = "pending"
    else:
        status = "complete"

    recommendations: list[str] = []
    if "assertion" in missing:
        recommendations.append(f"Add a vf:asserts annotation for {req.id}.")
    if "test" in missing:
        recommendations.append(
            f"Add a vf:test {req.id} <test_id> annotation and emit a VFTEST result marker."
        )
    if "coverage point" in missing:
        recommendations.append(f"Add a vf:covers annotation for {req.id}.")
    if any(item.startswith("coverage closure") for item in missing):
        if any(
            imported.get(cid, {}).get("source", "").endswith("coverage-ucis.csv")
            for cid in cover_ids
        ):
            recommendations.append(
                "Exercise the uncovered bins, then export and re-import the UCIS XML coverage."
            )
        else:
            recommendations.append("Exercise the uncovered bins, then update coverage.csv.")
    if any(item.startswith("coverage import") for item in pending):
        recommendations.append(
            "Import measured bins from coverage.csv or a simulator's UCIS XML export."
        )
    if "test run" in pending or "test result" in pending:
        recommendations.append("Run the testbench and record a VFTEST: <test_id> PASS marker.")
    if failures:
        recommendations.append("Inspect the saved simulator log and repair the failing test or assertion.")

    return {
        "id": req.id,
        "description": req.description,
        "priority": req.priority,
        "area": req.area,
        "required_evidence": list(req.required_evidence),
        "status": status,
        "evidence": {
            "assertions": declared["assertion"],
            "tests": [
                {"id": test_id, "status": run_tests.get(test_id, "not_run")}
                for test_id in declared["test"]
            ],
            "coverpoints": declared["coverage"],
            "coverage": coverage_summary,
        },
        "completed": completed,
        "missing": missing,
        "pending": pending,
        "failures": failures,
        "recommendations": recommendations,
    }


def analyze(project: Project, backend: str | None = None) -> dict:
    static = scan_sources(project)
    source_index = build_source_index(project, backend)
    coverage, coverage_warnings = load_coverage(project)
    known_ids = {req.id for req in project.requirements}
    for req_id, imported in coverage.items():
        if req_id not in known_ids:
            coverage_warnings.append(f"Coverage import references unknown requirement {req_id}")
            continue
        mapped_ids = set(static.coverage_ids.get(req_id, []))
        for coverage_id in imported:
            if coverage_id not in mapped_ids:
                coverage_warnings.append(
                    f"Coverage import {req_id}/{coverage_id} has no matching vf:covers annotation"
                )
    run = _load_run(project)
    requirements = [
        _evaluate_requirement(req, static, coverage, run)
        for req in project.requirements
    ]
    totals = {
        state: sum(item["status"] == state for item in requirements)
        for state in ("complete", "gap", "pending", "failing")
    }
    result = {
        "schema_version": 1,
        "project": project.name,
        "analysis": {"complete": totals["complete"], "total": len(requirements)},
        "summary": totals,
        "requirements": requirements,
        "sources": static.source_files,
        "warnings": static.warnings + coverage_warnings + source_index["warnings"],
        "simulation": run,
        "source_index": source_index,
    }
    return result


def render_markdown(result: dict) -> str:
    summary = result["summary"]
    lines = [
        f"# VeriForge report: {result['project']}",
        "",
        f"**Requirements complete:** {summary['complete']} / {result['analysis']['total']}",
        "",
        "| Requirement | Priority | Status | Evidence gaps |",
        "|---|---:|---|---|",
    ]
    for req in result["requirements"]:
        gaps = req["failures"] + req["missing"] + req["pending"]
        lines.append(
            f"| `{req['id']}` — {req['description']} | {req['priority']} | "
            f"**{req['status']}** | {', '.join(gaps) if gaps else '—'} |"
        )
    for req in result["requirements"]:
        lines.extend(["", f"## {req['id']}: {req['description']}", ""])
        if req["recommendations"]:
            lines.append("Next actions:")
            lines.extend(f"- {item}" for item in req["recommendations"])
        else:
            lines.append("Required evidence is present and closed.")
    if result["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result["warnings"])
    lines.append("")
    return "\n".join(lines)


def render_html(result: dict) -> str:
    """Render a self-contained, searchable local report."""
    summary = result["summary"]

    def esc(value) -> str:
        return html.escape(str(value if value is not None else ""), quote=True)

    rows = []
    details = []
    for req in result["requirements"]:
        search_text = " ".join((
            req["id"], req["description"], req["area"], req["status"],
            *req["missing"], *req["pending"], *req["failures"],
        ))
        gaps = req["failures"] + req["missing"] + req["pending"]
        rows.append(
            "<tr data-search=\"" + esc(search_text.casefold()) + "\">"
            f"<td><a href=\"#req-{esc(req['id'])}\"><code>{esc(req['id'])}</code></a>"
            f"<div class=\"muted\">{esc(req['description'])}</div></td>"
            f"<td>{esc(req['priority'])}</td>"
            f"<td><span class=\"status {esc(req['status'])}\">{esc(req['status'])}</span></td>"
            f"<td>{esc('; '.join(gaps) if gaps else '—')}</td></tr>"
        )

        evidence = req["evidence"]
        blocks = []
        if evidence["assertions"]:
            items = "".join(
                f"<li><code>{esc(item['source'])}:{esc(item['line'])}</code></li>"
                for item in evidence["assertions"]
            )
            blocks.append(f"<h4>Assertions</h4><ul>{items}</ul>")
        if evidence["tests"]:
            items = "".join(
                f"<li><code>{esc(item['id'])}</code> — {esc(item['status'])}</li>"
                for item in evidence["tests"]
            )
            blocks.append(f"<h4>Tests</h4><ul>{items}</ul>")
        if evidence["coverpoints"]:
            items = "".join(
                f"<li><code>{esc(item['id'])}</code> — "
                f"{esc(item['source'])}:{esc(item['line'])}</li>"
                for item in evidence["coverpoints"]
            )
            blocks.append(f"<h4>Declared coverpoints</h4><ul>{items}</ul>")
        if evidence["coverage"]:
            coverage_items = []
            for item in evidence["coverage"]:
                provenance = []
                if item.get("source_file"):
                    provenance.append(
                        f"source {esc(item['source_file'])}"
                        + (f":{esc(item['source_line'])}" if item.get("source_line") else "")
                    )
                if item.get("ucis_path"):
                    provenance.append(f"UCIS {esc(item['ucis_path'])}")
                if item.get("observed_hits") is not None:
                    provenance.append(f"{esc(item['observed_hits'])} observed hits")
                coverage_items.append(
                    f"<li><code>{esc(item['id'])}</code>: {esc(item['hits'])}/{esc(item['bins'])} bins"
                    + (f" <span class=\"muted\">({'; '.join(provenance)})</span>" if provenance else "")
                    + "</li>"
                )
            blocks.append(f"<h4>Measured coverage</h4><ul>{''.join(coverage_items)}</ul>")
        for label, values in (("Failures", req["failures"]), ("Missing evidence", req["missing"]), ("Pending", req["pending"])):
            if values:
                blocks.append(f"<h4>{esc(label)}</h4><ul>{''.join(f'<li>{esc(value)}</li>' for value in values)}</ul>")
        if req["recommendations"]:
            blocks.append(
                "<h4>Next actions</h4><ul>"
                + "".join(f"<li>{esc(value)}</li>" for value in req["recommendations"])
                + "</ul>"
            )
        details.append(
            f"<details class=\"requirement\" id=\"req-{esc(req['id'])}\" "
            f"data-search=\"{esc(search_text.casefold())}\">"
            f"<summary><span class=\"status {esc(req['status'])}\">{esc(req['status'])}</span> "
            f"<code>{esc(req['id'])}</code> — {esc(req['description'])}</summary>"
            f"<div class=\"detail-body\">{''.join(blocks) if blocks else '<p>Required evidence is present and closed.</p>'}</div>"
            "</details>"
        )

    warning_section = ""
    if result["warnings"]:
        warning_section = (
            "<section><h2>Warnings</h2><ul>"
            + "".join(f"<li>{esc(item)}</li>" for item in result["warnings"])
            + "</ul></section>"
        )
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VeriForge report: """ + esc(result["project"]) + """</title>
<style>
:root{color-scheme:light;--ink:#17233b;--muted:#65738b;--line:#dce3ec;--panel:#fff;--page:#f4f7fb;--accent:#2457d6}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:36px auto;padding:0 20px}h1,h2,h3,h4{line-height:1.25}h1{margin:0 0 4px;font-size:2rem}h2{margin:32px 0 12px;font-size:1.25rem}h4{margin:18px 0 4px;font-size:.95rem}
.muted,.subtitle{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:24px 0}.card,section,.requirement{background:var(--panel);border:1px solid var(--line);border-radius:12px}.card{padding:14px 16px}.card strong{display:block;font-size:1.6rem}.card span{text-transform:capitalize;color:var(--muted)}
.toolbar{margin:24px 0 10px}.toolbar input{width:100%;padding:11px 13px;border:1px solid var(--line);border-radius:9px;font:inherit}.table-wrap{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px}table{border-collapse:collapse;width:100%;min-width:700px}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}tr:last-child td{border-bottom:0}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;background:#eef2f8;padding:2px 5px;border-radius:4px}.status{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.78rem;font-weight:700;text-transform:capitalize}.status.complete{background:#d9f4e5;color:#17613a}.status.gap,.status.failing{background:#ffe2df;color:#9a2d23}.status.pending{background:#fff0c7;color:#755400}
.requirement{margin:10px 0;padding:0 14px}.requirement summary{cursor:pointer;padding:13px 0;font-weight:600}.detail-body{padding:0 0 14px 2px}.detail-body ul{margin:4px 0 8px;padding-left:22px}section{padding:16px 20px;margin:28px 0}.hidden{display:none!important}
@media(max-width:700px){main{margin:20px auto}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}h1{font-size:1.6rem}}
</style>
</head>
<body>
<main>
<header><h1>VeriForge report</h1><div class="subtitle">""" + esc(result["project"]) + """</div></header>
<div class="cards">
<div class="card"><strong>""" + esc(summary["complete"]) + """ / """ + esc(result["analysis"]["total"]) + """</strong><span>requirements complete</span></div>
<div class="card"><strong>""" + esc(summary["gap"]) + """</strong><span>gaps</span></div>
<div class="card"><strong>""" + esc(summary["pending"]) + """</strong><span>pending</span></div>
<div class="card"><strong>""" + esc(summary["failing"]) + """</strong><span>failing</span></div>
</div>
<h2>Requirements</h2>
<div class="table-wrap"><table><thead><tr><th>Requirement</th><th>Priority</th><th>Status</th><th>Evidence gaps</th></tr></thead><tbody>""" + "".join(rows) + """</tbody></table></div>
<div class="toolbar"><label for="filter">Filter requirements</label><input id="filter" type="search" placeholder="Search by ID, text, status, or gap"></div>
<h2>Evidence details</h2>
""" + "".join(details) + warning_section + """
</main>
<script>
const filter=document.getElementById('filter');
filter.addEventListener('input',()=>{const query=filter.value.trim().toLowerCase();document.querySelectorAll('[data-search]').forEach(item=>item.classList.toggle('hidden',!item.dataset.search.includes(query)));});
</script>
</body>
</html>
"""
