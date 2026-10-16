"""Import UCIS XML coverage into VeriForge's normalized coverage format."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from .project import Project, ProjectError
from .scanner import StaticEvidence


_BIN_TAGS = {"coverpointbin", "crossbin", "bin"}
_SOURCE_TAGS = {"cgsourceid", "cginstsourceid", "cpsourceid", "crosssourceid", "sourceid"}
_UCIS_MAP_COLUMNS = {"ucis_path", "requirement_id", "coverage_id"}
_OUTPUT_COLUMNS = (
    "requirement_id", "coverage_id", "hits", "bins", "source_file",
    "source_line", "ucis_path", "observed_hits", "bin_names",
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _integer(value: str | None, *, label: str, default: int | None = None) -> int | None:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ProjectError(f"UCIS XML has a non-integer {label}: {value!r}") from exc
    if parsed < 0:
        raise ProjectError(f"UCIS XML has a negative {label}: {parsed}")
    return parsed


def _threshold(element: ET.Element, inherited: int) -> int:
    for key in ("at_least", "atLeast", "atleast"):
        if key in element.attrib:
            value = _integer(element.attrib[key], label="at_least")
            if value is not None:
                return max(value, 1)
    option_elements = []
    for child in element:
        if _local(child.tag) in {"option", "optionvalue"}:
            option_elements.append(child)
        elif _local(child.tag) in {"options", "optionlist"}:
            option_elements.extend(
                item for item in child if _local(item.tag) in {"option", "optionvalue"}
            )
    for child in option_elements:
        option_name = (child.attrib.get("name") or child.attrib.get("key") or "").lower()
        if option_name in {"at_least", "atleast"}:
            value = _integer(
                child.attrib.get("value") or (child.text or ""), label="at_least"
            )
            if value is not None:
                return max(value, 1)
    return inherited


def _source_location(element: ET.Element, inherited: tuple[str | None, int | None], files: dict[str, str]):
    current_file, current_line = inherited
    for child in element:
        tag = _local(child.tag)
        if tag not in _SOURCE_TAGS:
            continue
        file_ref = child.attrib.get("file") or child.attrib.get("fileId")
        line = _integer(child.attrib.get("line"), label="source line")
        if file_ref:
            current_file = files.get(file_ref, file_ref)
        if line is not None:
            current_line = line
    return current_file, current_line


def _extract_bins(point: ET.Element, threshold: int) -> list[dict]:
    bins: list[dict] = []
    for element in point.iter():
        if element is point or _local(element.tag) not in _BIN_TAGS:
            continue
        name = (element.attrib.get("name") or element.attrib.get("binName") or "").strip()
        kind = (element.attrib.get("type") or _local(element.tag).removesuffix("bin") or "default").lower()
        counts: list[int] = []
        for child in element.iter():
            if _local(child.tag) != "contents":
                continue
            count = _integer(child.attrib.get("coverageCount"), label="coverageCount", default=0)
            if count is not None:
                counts.append(count)
        if not counts and "coverageCount" in element.attrib:
            counts.append(_integer(element.attrib.get("coverageCount"), label="coverageCount", default=0) or 0)
        count_total = sum(counts)
        bins.append({
            "name": name or f"bin_{len(bins) + 1}",
            "type": kind,
            "coverage_count": count_total,
            "excluded": "ignore" in kind or "illegal" in kind,
            "covered": count_total >= threshold,
        })
    return bins


def parse_ucis(path: str | Path) -> dict:
    """Parse a UCIS XML document and preserve point and bin provenance."""
    xml_path = Path(path).expanduser().resolve()
    try:
        raw = xml_path.read_bytes()
    except OSError as exc:
        raise ProjectError(f"Cannot read UCIS XML {xml_path}: {exc}") from exc
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProjectError(f"Cannot parse UCIS XML {xml_path}: {exc}") from exc
    if _local(root.tag) != "ucis":
        raise ProjectError(f"Expected a UCIS XML root element in {xml_path}")

    source_files: dict[str, str] = {}
    for element in root.iter():
        if _local(element.tag) == "sourcefile":
            file_id = element.attrib.get("id")
            file_name = element.attrib.get("fileName") or element.attrib.get("filename") or element.attrib.get("name")
            if file_id and file_name:
                source_files[file_id] = file_name

    points: list[dict] = []

    def visit(
        element: ET.Element,
        path_parts: tuple[str, ...],
        location: tuple[str | None, int | None],
        at_least: int,
        group_name: str | None,
    ) -> None:
        tag = _local(element.tag)
        current_location = _source_location(element, location, source_files)
        current_at_least = _threshold(element, at_least)
        current_group = group_name
        if tag == "cgid":
            current_group = element.attrib.get("cgName") or element.attrib.get("name") or current_group

        next_parts = path_parts
        if tag in {"instancecoverage", "cginstance", "covergroupcoverage", "covergroupinstance"}:
            name = element.attrib.get("name") or element.attrib.get("instanceName") or tag
            next_parts = (*path_parts, f"{tag}:{name}")
        elif tag in {"coverpoint", "cross"}:
            name = element.attrib.get("name") or element.attrib.get("alias") or f"{tag}_{len(points) + 1}"
            next_parts = (*path_parts, f"{tag}:{name}")
            aliases = []
            for key in ("name", "alias", "expr", "expression", "key"):
                value = element.attrib.get(key)
                if value and value not in aliases:
                    aliases.append(value)
            point_at_least = _threshold(element, current_at_least)
            source_file, source_line = _source_location(element, current_location, source_files)
            points.append({
                "ucis_path": "/" + "/".join(next_parts),
                "name": name,
                "aliases": aliases,
                "kind": tag,
                "covergroup": current_group,
                "source_file": source_file,
                "source_line": source_line,
                "at_least": point_at_least,
                "bins": _extract_bins(element, point_at_least),
            })
            return

        for child in element:
            visit(child, next_parts, current_location, current_at_least, current_group)

    visit(root, (), (None, None), 1, None)
    if not points:
        raise ProjectError(f"UCIS XML contains no coverpoints or crosses: {xml_path}")
    return {
        "ucis_version": root.attrib.get("ucisVersion") or root.attrib.get("version"),
        "source_xml": xml_path.name,
        "source_xml_sha256": hashlib.sha256(raw).hexdigest(),
        "source_files": source_files,
        "coverage_points": points,
    }


def _read_mapping(path: Path, static: StaticEvidence) -> dict[str, list[dict[str, str]]]:
    mappings: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            missing = sorted(_UCIS_MAP_COLUMNS - set(reader.fieldnames or ()))
            if missing:
                raise ProjectError(f"{path.name} is missing columns: {', '.join(missing)}")
            for row_number, row in enumerate(reader, start=2):
                ucis_path = (row.get("ucis_path") or "").strip()
                req_id = (row.get("requirement_id") or "").strip()
                coverage_id = (row.get("coverage_id") or "").strip()
                if not ucis_path or not req_id or not coverage_id:
                    raise ProjectError(
                        f"{path.name}:{row_number}: ucis_path, requirement_id, and coverage_id are required"
                    )
                if coverage_id not in static.coverage_ids.get(req_id, []):
                    raise ProjectError(
                        f"{path.name}:{row_number}: {req_id}/{coverage_id} has no matching vf:covers annotation"
                    )
                key = (ucis_path, req_id, coverage_id)
                if key in seen:
                    raise ProjectError(f"{path.name}:{row_number}: duplicate UCIS mapping")
                seen.add(key)
                mappings[ucis_path].append({"requirement_id": req_id, "coverage_id": coverage_id})
    except OSError as exc:
        raise ProjectError(f"Cannot read UCIS mapping {path}: {exc}") from exc
    return mappings


def _map_points(parsed: dict, static: StaticEvidence, mapping_path: Path | None):
    explicit = _read_mapping(mapping_path, static) if mapping_path else {}
    existing_paths = {point["ucis_path"] for point in parsed["coverage_points"]}
    unknown_paths = sorted(set(explicit) - existing_paths)
    if unknown_paths:
        raise ProjectError(
            f"UCIS mapping references path(s) not present in this XML: {', '.join(unknown_paths)}"
        )
    requirements_by_id: dict[str, list[str]] = defaultdict(list)
    for req_id, ids in static.coverage_ids.items():
        for coverage_id in ids:
            requirements_by_id[coverage_id].append(req_id)

    mapped: list[dict] = []
    unmatched: list[dict] = []
    warnings: list[str] = []
    for point in parsed["coverage_points"]:
        mappings = explicit.get(point["ucis_path"])
        reason = ""
        if mappings is None:
            aliases = {value.casefold() for value in point["aliases"]}
            candidates = sorted(
                coverage_id
                for coverage_id in requirements_by_id
                if coverage_id.casefold() in aliases
            )
            if len(candidates) == 1:
                coverage_id = candidates[0]
                mappings = [
                    {"requirement_id": req_id, "coverage_id": coverage_id}
                    for req_id in sorted(requirements_by_id[coverage_id])
                ]
            elif len(candidates) > 1:
                reason = "ambiguous automatic match: " + ", ".join(candidates)
            else:
                reason = "no matching vf:covers id; add a row to the mapping CSV"
        if mappings is not None and not point["bins"]:
            reason = "point has no measurable bins"
            mappings = None
        if mappings is None:
            item = {**point, "reason": reason or "not mapped"}
            unmatched.append(item)
            warnings.append(f"Unmapped UCIS point {point['ucis_path']}: {item['reason']}")
            continue
        for mapping in mappings:
            mapped.append({
                "ucis_path": point["ucis_path"],
                "requirement_id": mapping["requirement_id"],
                "coverage_id": mapping["coverage_id"],
                "point": point,
            })
    return mapped, unmatched, warnings


def _coverage_rows(mapped: list[dict]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], dict[tuple[str, str, str, str], dict]] = defaultdict(dict)
    paths: dict[tuple[str, str], set[str]] = defaultdict(set)
    files: dict[tuple[str, str], set[str]] = defaultdict(set)
    lines: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in mapped:
        pair = (item["requirement_id"], item["coverage_id"])
        point = item["point"]
        paths[pair].add(point["ucis_path"])
        if point.get("source_file"):
            files[pair].add(str(point["source_file"]))
        if point.get("source_line") is not None:
            lines[pair].add(str(point["source_line"]))
        for bin_info in point["bins"]:
            if bin_info["excluded"]:
                continue
            key = (
                str(point.get("source_file") or ""),
                str(point.get("source_line") or ""),
                bin_info["name"],
                bin_info["type"],
            )
            previous = grouped[pair].get(key)
            if previous is None:
                grouped[pair][key] = {**bin_info}
            else:
                previous["coverage_count"] = max(previous["coverage_count"], bin_info["coverage_count"])
                previous["covered"] = previous["covered"] or bin_info["covered"]

    rows = []
    for pair in sorted(grouped):
        bins = grouped[pair]
        entries = [bins[key] for key in sorted(bins)]
        if not entries:
            continue
        rows.append({
            "requirement_id": pair[0],
            "coverage_id": pair[1],
            "hits": str(sum(item["covered"] for item in entries)),
            "bins": str(len(entries)),
            "source_file": ";".join(sorted(files[pair])),
            "source_line": ";".join(sorted(lines[pair], key=lambda value: int(value))),
            "ucis_path": ";".join(sorted(paths[pair])),
            "observed_hits": str(sum(item["coverage_count"] for item in entries)),
            "bin_names": json.dumps([item["name"] for item in entries], separators=(",", ":")),
        })
    return rows


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_name = stream.name
            stream.write(contents)
        os.replace(temporary_name, path)
    except OSError:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def import_ucis_file(
    project: Project,
    xml_path: str | Path,
    static: StaticEvidence,
    mapping_path: str | Path | None = None,
) -> dict:
    """Parse, map, and write a UCIS import without modifying coverage.csv."""
    parsed = parse_ucis(xml_path)
    mapped, unmatched, warnings = _map_points(
        parsed, static, Path(mapping_path).expanduser().resolve() if mapping_path else None
    )
    rows = _coverage_rows(mapped)
    document = {
        "schema_version": 1,
        "format": "UCIS XML",
        **parsed,
        "mapped_points": [
            {key: value for key, value in item.items() if key != "point"}
            for item in mapped
        ],
        "unmatched_points": unmatched,
        "coverage_rows": rows,
        "warnings": warnings,
    }
    metadata_path = project.root / ".veriforge" / "ucis-import.json"
    coverage_path = project.root / ".veriforge" / "coverage-ucis.csv"
    _atomic_write(metadata_path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    from io import StringIO
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_OUTPUT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    _atomic_write(coverage_path, stream.getvalue())
    return {
        "metadata_path": metadata_path,
        "coverage_path": coverage_path,
        "point_count": len(parsed["coverage_points"]),
        "mapped_count": len({item["ucis_path"] for item in mapped}),
        "row_count": len(rows),
        "unmatched": unmatched,
        "warnings": warnings,
    }
