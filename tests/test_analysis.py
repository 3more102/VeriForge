import csv
import json
import tempfile
import unittest
from pathlib import Path

from veriforge.analysis import analyze, render_html
from veriforge.cli import _init_project
from veriforge.project import load_project


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = _init_project(Path(self.temp.name) / "demo")

    def tearDown(self):
        self.temp.cleanup()

    def test_finds_static_evidence_but_reports_unrun_and_unmeasured_gaps(self):
        result = analyze(load_project(self.root))
        self.assertEqual(result["summary"]["pending"], 2)
        self.assertEqual(result["summary"]["complete"], 0)
        first = result["requirements"][0]
        self.assertEqual(len(first["evidence"]["assertions"]), 2)
        self.assertEqual(first["evidence"]["tests"][0]["id"], "rv_buffer_backpressure_and_refill")
        self.assertTrue(any(item.startswith("coverage import") for item in first["pending"]))

    def test_marks_requirement_complete_with_passed_test_and_full_coverage(self):
        run_dir = self.root / ".veriforge"
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps({
            "status": "passed",
            "tests": [{"id": "rv_buffer_backpressure_and_refill", "status": "pass"}],
        }))
        with (self.root / "coverage.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["requirement_id", "coverage_id", "hits", "bins"])
            writer.writerow(["REQ-RV-001", "blocked_valid", 2, 2])
            writer.writerow(["REQ-RV-002", "simultaneous_consume_refill", 3, 3])
        result = analyze(load_project(self.root))
        self.assertEqual(result["summary"]["complete"], 2)
        self.assertEqual([item["status"] for item in result["requirements"]], ["complete", "complete"])

    def test_reports_failed_mapped_test(self):
        run_dir = self.root / ".veriforge"
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps({
            "status": "failed",
            "tests": [{"id": "rv_buffer_backpressure_and_refill", "status": "fail"}],
        }))
        result = analyze(load_project(self.root))
        self.assertEqual(result["summary"]["failing"], 2)
        self.assertEqual(result["requirements"][0]["status"], "failing")

    def test_reports_partial_coverage_as_gap(self):
        with (self.root / "coverage.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["requirement_id", "coverage_id", "hits", "bins"])
            writer.writerow(["REQ-RV-001", "blocked_valid", 1, 2])
        result = analyze(load_project(self.root))
        first = result["requirements"][0]
        self.assertEqual(first["status"], "gap")
        self.assertIn("coverage closure (1/2 bins hit)", first["missing"])

    def test_manual_coverage_csv_takes_precedence_over_ucis_duplicate(self):
        with (self.root / "coverage.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["requirement_id", "coverage_id", "hits", "bins"])
            writer.writerow(["REQ-RV-001", "blocked_valid", 0, 2])
        metadata = self.root / ".veriforge"
        metadata.mkdir(exist_ok=True)
        (metadata / "coverage-ucis.csv").write_text(
            "requirement_id,coverage_id,hits,bins\nREQ-RV-001,blocked_valid,2,2\n"
        )
        result = analyze(load_project(self.root))
        coverage = result["requirements"][0]["evidence"]["coverage"][0]
        self.assertEqual(coverage["hits"], 0)
        self.assertEqual(coverage["source"], "coverage.csv")
        self.assertTrue(any("duplicates REQ-RV-001/blocked_valid" in warning for warning in result["warnings"]))

    def test_requires_every_mapped_test_to_pass(self):
        source = self.root / "tb/tb_top.sv"
        source.write_text(source.read_text().replace(
            "rv_buffer_backpressure_and_refill\n", "rv_buffer_backpressure_and_refill\n"
        ) + "\n// vf:test REQ-RV-001 extra_test\n")
        run_dir = self.root / ".veriforge"
        run_dir.mkdir(exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({
            "status": "passed",
            "tests": [{"id": "rv_buffer_backpressure_and_refill", "status": "pass"}],
        }))
        result = analyze(load_project(self.root))
        first = result["requirements"][0]
        self.assertEqual(first["status"], "pending")
        self.assertIn("results for all mapped tests", first["pending"])

    def test_html_report_is_self_contained_searchable_and_escapes_source_text(self):
        result = analyze(load_project(self.root))
        result["requirements"][0]["description"] = "Unsafe <script>alert(1)</script>"
        output = render_html(result)
        self.assertIn("id=\"filter\"", output)
        self.assertIn("data-search=", output)
        self.assertIn("Unsafe &lt;script&gt;alert(1)&lt;/script&gt;", output)
        self.assertNotIn("Unsafe <script>", output)
        self.assertNotIn("https://", output)

    def test_analyze_command_writes_markdown_json_and_html(self):
        from veriforge.cli import main

        self.assertEqual(main(["analyze", str(self.root)]), 0)
        reports = self.root / "reports"
        self.assertTrue((reports / "veriforge-report.md").is_file())
        self.assertTrue((reports / "veriforge-report.json").is_file())
        html_report = (reports / "veriforge-report.html").read_text()
        self.assertIn("<title>VeriForge report: demo</title>", html_report)


if __name__ == "__main__":
    unittest.main()
