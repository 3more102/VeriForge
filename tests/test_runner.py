import unittest
import json
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from veriforge.cli import _init_project
from veriforge.project import load_project
from veriforge.runner import evaluate_run, parse_test_markers, run_verilator


class RunnerTests(unittest.TestCase):
    def test_parses_and_deduplicates_test_markers(self):
        parsed = parse_test_markers(
            "VFTEST: smoke PASS\nnoise\nVFTEST: broken FAIL crc mismatch\nVFTEST: smoke PASS"
        )
        self.assertEqual(parsed, [
            {"id": "broken", "status": "fail", "detail": "crc mismatch"},
            {"id": "smoke", "status": "pass", "detail": ""},
        ])

    def test_run_fails_when_no_test_marker_was_emitted(self):
        status, message = evaluate_run(0, 0, [])
        self.assertEqual(status, "failed")
        self.assertIn("no VFTEST", message)

    def test_run_fails_when_test_marker_fails_even_if_process_exits_zero(self):
        status, message = evaluate_run(0, 0, [{"id": "crc_bad", "status": "fail"}])
        self.assertEqual(status, "failed")
        self.assertIn("crc_bad", message)

    def test_verilator_run_records_pass_log_and_waveform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            project = load_project(root)
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                if command[0] == "/usr/bin/verilator":
                    (root / ".veriforge/build/Vtb_top").write_text("fake executable")
                    return CompletedProcess(command, 0, "build succeeded\n")
                (root / "waveform.vcd").write_text("$enddefinitions $end\n")
                return CompletedProcess(command, 0, "VFTEST: rv_buffer_backpressure_and_refill PASS\n")

            with patch("veriforge.runner.shutil.which", return_value="/usr/bin/verilator"), \
                 patch("veriforge.runner.subprocess.run", side_effect=fake_run):
                code, result = run_verilator(project)

            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["waveform"], "waveform.vcd")
            self.assertIn("--trace", calls[0])
            self.assertIn("--coverage", calls[0])
            stored = json.loads((root / ".veriforge/run.json").read_text())
            self.assertEqual(stored["tests"][0]["status"], "pass")
            self.assertIn("VFTEST", (root / "logs/verilator.log").read_text())

    def test_missing_verilator_is_recorded_as_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            with patch("veriforge.runner.shutil.which", return_value=None):
                code, result = run_verilator(load_project(root))
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue((root / ".veriforge/run.json").is_file())


if __name__ == "__main__":
    unittest.main()
