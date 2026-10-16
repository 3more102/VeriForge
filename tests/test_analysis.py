import csv
import json
import tempfile
import unittest
from pathlib import Path

from veriforge.analysis import analyze
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


if __name__ == "__main__":
    unittest.main()
