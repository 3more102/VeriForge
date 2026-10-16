import unittest
import json
import tempfile
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from veriforge.cli import _init_project, main
from veriforge.project import load_project
from veriforge.history import list_runs
from veriforge.runner import evaluate_run, parse_test_markers, run_simulation, run_verilator


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
            self.assertEqual(stored["run_id"], 1)
            self.assertIn("VFTEST", (root / "logs/verilator.log").read_text())
            history = list_runs(root)
            self.assertEqual(history[0]["status"], "passed")
            self.assertEqual(history[0]["test_count"], 1)
            self.assertEqual(history[0]["passed_tests"], 1)

    def test_icarus_run_records_pass_and_uses_vvp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            self.assertEqual(main(["configure", "--simulator", "icarus", str(root)]), 0)
            project = load_project(root)
            calls = []
            execution_dir = root / ".veriforge" / "regression" / "case_a"
            execution_dir.mkdir(parents=True)
            project = replace(project, waveform_file=execution_dir / "waveform.vcd")

            def fake_run(command, **kwargs):
                calls.append(command)
                if command[0] == "/usr/bin/iverilog":
                    output = Path(command[command.index("-o") + 1])
                    output.write_text("fake bytecode")
                    return CompletedProcess(command, 0, "build succeeded\n")
                (Path(kwargs["cwd"]) / "waveform.vcd").write_text("$enddefinitions $end\n")
                return CompletedProcess(command, 0, "VFTEST: smoke PASS\n")

            with patch("veriforge.runner.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), \
                 patch("veriforge.runner.subprocess.run", side_effect=fake_run):
                code, result = run_simulation(
                    project,
                    runtime_args=["+sample=1"],
                    scenario="case_a",
                    seed=1,
                    execution_dir=execution_dir,
                )

            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["simulator"], "icarus")
            self.assertEqual(result["waveform"], ".veriforge/regression/case_a/waveform.vcd")
            self.assertIn("-g2012", calls[0])
            self.assertEqual(calls[1][0], "/usr/bin/vvp")
            self.assertEqual(calls[1][-1], "+sample=1")
            self.assertTrue((root / "logs/icarus-case_a.log").is_file())
            history = list_runs(root)[0]
            self.assertEqual(history["simulator"], "icarus")
            self.assertEqual(history["scenario"], "case_a")
            self.assertEqual(history["seed"], 1)
            self.assertEqual(json.loads(history["arguments_json"]), ["+sample=1"])
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["history", str(root)]), 0)
            self.assertIn("1/1 tests passed", output.getvalue())
            self.assertIn("[case_a] seed=1", output.getvalue())

    def test_missing_verilator_is_recorded_as_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            (root / "waveform.vcd").write_text("stale waveform")
            with patch("veriforge.runner.shutil.which", return_value=None):
                code, result = run_verilator(load_project(root))
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue((root / ".veriforge/run.json").is_file())
            self.assertEqual(list_runs(root)[0]["status"], "blocked")
            self.assertFalse((root / "waveform.vcd").exists())


if __name__ == "__main__":
    unittest.main()
