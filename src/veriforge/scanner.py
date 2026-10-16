"""Extract traceable evidence from lightweight SystemVerilog annotations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .project import Project


_DIRECTIVE = re.compile(
    r"^\s*//\s*vf:(asserts|covers|test)\s+([^\s]+)(?:\s+(.*?))?\s*$",
    re.IGNORECASE,
)
_ASSERTION = re.compile(r"\bassert\b", re.IGNORECASE)
_COVER = re.compile(r"\b(?:cover|coverpoint|covergroup)\b", re.IGNORECASE)
_BLOCK_COMMENT_START = re.compile(r"/\*")
_BLOCK_COMMENT_END = re.compile(r"\*/")
_ID_LIST = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:,[A-Za-z][A-Za-z0-9_-]*)*$")


@dataclass
class StaticEvidence:
    assertions: dict[str, list[dict]] = field(default_factory=dict)
    coverpoints: dict[str, list[dict]] = field(default_factory=dict)
    tests: dict[str, list[str]] = field(default_factory=dict)
    coverage_ids: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)


def _strip_comments(line: str, in_block: bool) -> tuple[str, bool]:
    if in_block:
        end = _BLOCK_COMMENT_END.search(line)
        if end is None:
            return "", True
        line = line[end.end():]
        in_block = False
    while True:
        start = _BLOCK_COMMENT_START.search(line)
        if start is None:
            break
        end = _BLOCK_COMMENT_END.search(line, start.end())
        if end is None:
            line = line[:start.start()]
            in_block = True
            break
        line = line[:start.start()] + line[end.end():]
    return line.split("//", 1)[0], in_block


def scan_sources(project: Project) -> StaticEvidence:
    result = StaticEvidence()
    known_ids = {req.id for req in project.requirements}

    for source in project.sources:
        try:
            relative = source.relative_to(project.root).as_posix()
        except ValueError:
            relative = source.as_posix()
        result.source_files.append(relative)
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            result.warnings.append(f"Could not scan {relative}: {exc}")
            continue

        in_block = False
        code_lines: dict[int, str] = {}
        directives: list[tuple[int, str, str, str]] = []
        for line_number, line in enumerate(lines, start=1):
            if not in_block:
                match = _DIRECTIVE.match(line)
                if match:
                    directives.append(
                        (line_number, match.group(1).lower(), match.group(2), match.group(3) or "")
                    )
                    code_lines[line_number] = ""
                    continue
            code, in_block = _strip_comments(line, in_block)
            code_lines[line_number] = code

        for line_number, kind, targets, detail in directives:
            if kind == "test":
                test_id = detail.split()[0] if detail.strip() else f"unlabeled-test@{line_number}"
                if not _ID_LIST.fullmatch(targets):
                    result.warnings.append(
                        f"{relative}:{line_number}: malformed requirement id list {targets!r}"
                    )
                    continue
                target_ids = targets.split(",")
                for req_id in target_ids:
                    if req_id not in known_ids:
                        result.warnings.append(
                            f"{relative}:{line_number}: annotation references unknown requirement {req_id}"
                        )
                        continue
                    result.tests.setdefault(req_id, []).append(test_id)
                continue

            statement_match = _ASSERTION if kind == "asserts" else _COVER
            statement_line: int | None = None
            for candidate_line in range(line_number + 1, min(len(lines), line_number + 12) + 1):
                code = code_lines.get(candidate_line, "").strip()
                if not code:
                    continue
                if statement_match.search(code):
                    statement_line = candidate_line
                break
            if statement_line is None:
                result.warnings.append(
                    f"{relative}:{line_number}: vf:{kind} is not followed by a matching "
                    f"SystemVerilog statement within 11 lines"
                )
                continue

            if not _ID_LIST.fullmatch(targets):
                result.warnings.append(
                    f"{relative}:{line_number}: malformed requirement id list {targets!r}"
                )
                continue
            target_ids = targets.split(",")
            extra = dict(token.split("=", 1) for token in detail.split() if "=" in token)
            evidence_id = extra.get("id", f"{relative}:{statement_line}")
            evidence_item = {
                "id": evidence_id,
                "source": relative,
                "line": statement_line,
                "annotation_line": line_number,
            }
            destination = result.assertions if kind == "asserts" else result.coverpoints
            for req_id in target_ids:
                if req_id not in known_ids:
                    result.warnings.append(
                        f"{relative}:{line_number}: annotation references unknown requirement {req_id}"
                    )
                    continue
                destination.setdefault(req_id, []).append(evidence_item.copy())
                if kind == "covers":
                    result.coverage_ids.setdefault(req_id, []).append(evidence_id)

    for req_id in list(result.tests):
        result.tests[req_id] = sorted(set(result.tests[req_id]))
    for req_id in list(result.coverage_ids):
        result.coverage_ids[req_id] = sorted(set(result.coverage_ids[req_id]))
    return result
