import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from veriforge.cli import _init_project, main
from veriforge.history import history_path, list_runs, record_run
from veriforge.project import ProjectError, load_project
from veriforge.regression import load_regression, run_regression


class RegressionTests(unittest.TestCase):
    def test_init_includes_named_seed_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            project = load_project(root)
            manifest = load_regression(project.regression_file)
            self.assertEqual(manifest.name, "ready_valid_seed_sweep")
            self.assertEqual([(case.name, case.seed) for case in manifest.cases], [
                ("seed_1", 1),
                ("seed_2", 2),
            ])

    def test_rejects_duplicate_names_and_out_of_range_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            project = load_project(root)
            manifest_path = project.regression_file
            manifest_path.write_text(
                "[[case]]\nname='same'\nseed=1\n[[case]]\nname='same'\nseed=2\n"
            )
            with self.assertRaisesRegex(ProjectError, "Duplicate regression case"):
                load_regression(manifest_path)
            manifest_path.write_text("[[case]]\nname='bad_seed'\nseed=-1\n")
            with self.assertRaisesRegex(ProjectError, "seed must be an integer"):
                load_regression(manifest_path)

    def test_regression_records_case_seed_arguments_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            project = load_project(root)
            calls = []

            def fake_run(case_project, **kwargs):
                calls.append(kwargs)
                execution_dir = kwargs["execution_dir"]
                waveform = case_project.waveform_file
                waveform.parent.mkdir(parents=True, exist_ok=True)
                waveform.write_text("waveform")
                return 0, {
                    "status": "passed",
                    "simulator": "verilator",
                    "run_id": len(calls),
                    "tests": [{"id": "smoke", "status": "pass"}],
                    "waveform": waveform.relative_to(root).as_posix(),
                    "message": None,
                }

            with patch("veriforge.regression.run_simulation", side_effect=fake_run):
                code, summary = run_regression(project)

            self.assertEqual(code, 0)
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(summary["passed_cases"], 2)
            self.assertEqual(calls[0]["scenario"], "seed_1")
            self.assertEqual(calls[0]["seed"], 1)
            self.assertEqual(calls[0]["runtime_args"], ["+veriforge_seed=1"])
            self.assertIn("seed_2/waveform.vcd", summary["cases"][1]["waveform"])
            stored = json.loads((root / ".veriforge/regression.json").read_text())
            self.assertEqual(stored["case_count"], 2)

    def test_regress_cli_reports_blocked_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")

            def fake_run(_project, **kwargs):
                return 2, {
                    "status": "blocked",
                    "simulator": "verilator",
                    "run_id": 1 if kwargs["seed"] == 1 else 2,
                    "tests": [],
                    "waveform": None,
                    "message": "Verilator missing",
                }

            output = StringIO()
            with patch("veriforge.regression.run_simulation", side_effect=fake_run), \
                 redirect_stdout(output):
                code = main(["regress", str(root)])
            self.assertEqual(code, 2)
            self.assertIn("0/2 cases passed", output.getvalue())
            self.assertEqual(output.getvalue().count("blocked"), 3)

    def test_history_adds_regression_columns_to_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = history_path(root)
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """CREATE TABLE runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        started_at TEXT NOT NULL,
                        simulator TEXT NOT NULL,
                        top_module TEXT NOT NULL,
                        status TEXT NOT NULL,
                        duration_seconds REAL NOT NULL,
                        return_code INTEGER,
                        waveform TEXT,
                        log_path TEXT NOT NULL,
                        message TEXT
                    )"""
                )
                connection.execute(
                    """CREATE TABLE run_tests (
                        run_id INTEGER NOT NULL,
                        test_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        PRIMARY KEY (run_id, test_id)
                    )"""
                )
            record_run(
                root,
                {
                    "started_at": "2026-10-16T10:00:00+00:00",
                    "simulator": "icarus",
                    "top_module": "tb_top",
                    "status": "passed",
                    "duration_seconds": 0.1,
                    "return_code": 0,
                    "tests": [],
                    "scenario": "seed_3",
                    "seed": 3,
                    "arguments": ["+veriforge_seed=3"],
                },
                "logs/icarus-seed_3.log",
            )
            run = list_runs(root)[0]
            self.assertEqual(run["scenario"], "seed_3")
            self.assertEqual(run["seed"], 3)
            self.assertEqual(json.loads(run["arguments_json"]), ["+veriforge_seed=3"])


if __name__ == "__main__":
    unittest.main()
