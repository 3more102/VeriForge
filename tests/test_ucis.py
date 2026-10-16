import csv
import json
import tempfile
import unittest
from pathlib import Path

from veriforge.analysis import analyze, render_html
from veriforge.cli import _init_project, main
from veriforge.project import ProjectError, load_project
from veriforge.scanner import scan_sources
from veriforge.ucis import import_ucis_file, parse_ucis


_UCIS_XML = '''<?xml version="1.0"?>
<UCIS xmlns="urn:accellera:ucis:1.0" ucisVersion="1.0">
  <sourceFiles>
    <sourceFile id="1" fileName="tb/tb_top.sv"/>
  </sourceFiles>
  <instanceCoverages>
    <instanceCoverage name="tb_top">
      <covergroupCoverage name="rv_cg">
        <cgInstance name="cg0">
          <cgId cgName="rv_cg"/>
          <cgSourceId file="1" line="41"/>
          <coverpoint name="blocked_valid" at_least="2">
            <coverpointBin name="blocked" type="default"><contents coverageCount="3"/></coverpointBin>
            <coverpointBin name="not_blocked" type="default"><contents coverageCount="1"/></coverpointBin>
            <coverpointBin name="ignored" type="ignore"><contents coverageCount="99"/></coverpointBin>
          </coverpoint>
          <coverpoint name="vendor_alias">
            <cpSourceId file="1" line="72"/>
            <coverpointBin name="seen" type="default"><contents coverageCount="1"/></coverpointBin>
          </coverpoint>
        </cgInstance>
      </covergroupCoverage>
    </instanceCoverage>
  </instanceCoverages>
</UCIS>
'''


class UcisImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = _init_project(Path(self.temp.name) / "demo")
        self.xml = Path(self.temp.name) / "coverage.xml"
        self.xml.write_text(_UCIS_XML, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_parser_preserves_source_and_applies_threshold_and_exclusions(self):
        parsed = parse_ucis(self.xml)
        point = parsed["coverage_points"][0]
        self.assertEqual(point["source_file"], "tb/tb_top.sv")
        self.assertEqual(point["source_line"], 41)
        self.assertEqual(point["at_least"], 2)
        self.assertEqual([item["name"] for item in point["bins"]], ["blocked", "not_blocked", "ignored"])
        self.assertEqual([item["covered"] for item in point["bins"]], [True, False, True])
        self.assertTrue(point["bins"][2]["excluded"])
        self.assertEqual(parsed["coverage_points"][1]["at_least"], 1)
        self.assertTrue(parsed["coverage_points"][1]["bins"][0]["covered"])
        self.assertTrue(parsed["source_xml_sha256"])

    def test_import_auto_maps_by_coverage_id_and_analysis_exposes_provenance(self):
        project = load_project(self.root)
        result = import_ucis_file(project, self.xml, scan_sources(project))
        self.assertEqual(result["point_count"], 2)
        self.assertEqual(result["mapped_count"], 1)
        self.assertEqual(len(result["unmatched"]), 1)
        self.assertEqual(
            (self.root / "coverage.csv").read_text(),
            "requirement_id,coverage_id,hits,bins\n",
        )

        with (self.root / ".veriforge/coverage-ucis.csv").open(newline="") as stream:
            imported = list(csv.DictReader(stream))
        self.assertEqual(imported[0]["coverage_id"], "blocked_valid")
        self.assertEqual((imported[0]["hits"], imported[0]["bins"]), ("1", "2"))
        self.assertEqual(imported[0]["source_file"], "tb/tb_top.sv")
        self.assertEqual(imported[0]["source_line"], "41")

        report = analyze(project)
        evidence = report["requirements"][0]["evidence"]["coverage"][0]
        self.assertEqual(evidence["source"], ".veriforge/coverage-ucis.csv")
        self.assertEqual(evidence["source_line"], "41")
        self.assertIn("Unmapped UCIS point", "\n".join(report["warnings"]))
        self.assertIn("source tb/tb_top.sv:41", render_html(report))

    def test_explicit_mapping_resolves_nonmatching_vendor_name(self):
        project = load_project(self.root)
        parsed = parse_ucis(self.xml)
        custom_path = parsed["coverage_points"][1]["ucis_path"]
        mapping = self.root / "ucis-map.csv"
        mapping.write_text(
            "ucis_path,requirement_id,coverage_id\n"
            f"{custom_path},REQ-RV-002,simultaneous_consume_refill\n",
            encoding="utf-8",
        )
        result = import_ucis_file(project, self.xml, scan_sources(project), mapping)
        self.assertEqual(result["mapped_count"], 2)
        self.assertEqual(result["unmatched"], [])
        with (self.root / ".veriforge/coverage-ucis.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({row["coverage_id"] for row in rows}, {
            "blocked_valid", "simultaneous_consume_refill"
        })

    def test_mapping_requires_an_existing_vf_annotation(self):
        project = load_project(self.root)
        custom_path = parse_ucis(self.xml)["coverage_points"][1]["ucis_path"]
        mapping = self.root / "bad-map.csv"
        mapping.write_text(
            "ucis_path,requirement_id,coverage_id\n"
            f"{custom_path},REQ-RV-002,misspelled_id\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ProjectError, "no matching vf:covers"):
            import_ucis_file(project, self.xml, scan_sources(project), mapping)

    def test_rejects_invalid_xml_and_wrong_root(self):
        bad = self.root / "bad.xml"
        bad.write_text("<UCIS>", encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "Cannot parse UCIS XML"):
            parse_ucis(bad)
        bad.write_text("<coverage/>", encoding="utf-8")
        with self.assertRaisesRegex(ProjectError, "Expected a UCIS"):
            parse_ucis(bad)

    def test_cli_coverage_import_writes_outputs(self):
        self.assertEqual(
            main(["coverage", "import", str(self.xml), "--project", str(self.root)]), 1
        )
        self.assertTrue((self.root / ".veriforge/ucis-import.json").exists())
        metadata = json.loads((self.root / ".veriforge/ucis-import.json").read_text())
        self.assertEqual(metadata["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
