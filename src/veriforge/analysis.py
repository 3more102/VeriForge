"""Requirement-level gap and closure analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .project import Project, Requirement, ProjectError
from .scanner import StaticEvidence, scan_sources
from .source_index import build_source_index


_COVERAGE_COLUMNS = {"requirement_id", "coverage_id", "hits", "bins"}


def load_coverage(project: Project) -> tuple[dict[str, dict[str, dict]], list[str]]:
    if not project.coverage_file.exists():
        required = any("coverage" in req.required_evidence for req in project.requirements)
        warnings = [f"Coverage import not found: {project.coverage_file.name}"] if required else []
        return {}, warnings
    records: dict[str, dict[str, dict]] = {}
    warnings: list[str] = []
    try:
        with project.coverage_file.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            missing = sorted(_COVERAGE_COLUMNS - set(reader.fieldnames or ()))
            if missing:
                raise ProjectError(
                    f"{project.coverage_file.name} is missing columns: {', '.join(missing)}"
                )
            for row_number, row in enumerate(reader, start=2):
                req_id = (row.get("requirement_id") or "").strip()
                coverage_id = (row.get("coverage_id") or "").strip()
                try:
                    hits = int((row.get("hits") or "").strip())
                    bins = int((row.get("bins") or "").strip())
                except ValueError as exc:
                    raise ProjectError(
                        f"{project.coverage_file.name}:{row_number}: hits and bins must be integers"
                    ) from exc
                if not req_id or not coverage_id:
                    raise ProjectError(
                        f"{project.coverage_file.name}:{row_number}: requirement_id and coverage_id are required"
                    )
                if hits < 0 or bins < 1 or hits > bins:
                    raise ProjectError(
                        f"{project.coverage_file.name}:{row_number}: expected 0 <= hits <= bins and bins >= 1"
                    )
                if coverage_id in records.setdefault(req_id, {}):
                    raise ProjectError(
                        f"{project.coverage_file.name}:{row_number}: duplicate coverage id {coverage_id}"
                    )
                records[req_id][coverage_id] = {
                    "hits": hits,
                    "bins": bins,
                    "source": project.coverage_file.name,
                    "line": row_number,
                }
    except OSError as exc:
        raise ProjectError(f"Cannot read coverage import {project.coverage_file}: {exc}") from exc
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
            coverage_summary = [
                {"id": cid, "hits": imported[cid]["hits"], "bins": imported[cid]["bins"]}
                for cid in cover_ids if cid in imported
            ]
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
        recommendations.append("Exercise the uncovered bins, then refresh coverage.csv.")
    if any(item.startswith("coverage import") for item in pending):
        recommendations.append("Import measured bins into coverage.csv using the documented format.")
    if "test run" in pending or "test result" in pending:
        recommendations.append("Run the testbench and record a VFTEST: <test_id> PASS marker.")
    if failures:
        recommendations.append("Inspect the saved Verilator log and repair the failing test or assertion.")

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
