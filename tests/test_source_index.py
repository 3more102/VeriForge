import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from veriforge.cli import _init_project
from veriforge.project import load_project
from veriforge.source_index import build_source_index


_CST = {
    "syntaxTrees": [{
        "kind": "SyntaxTree",
        "root": {
            "kind": "CompilationUnit",
            "members": [
                {
                    "kind": "ModuleDeclaration",
                    "header": {
                        "kind": "ModuleHeader",
                        "name": {"kind": "Identifier", "text": "sample_top"},
                    },
                },
                {
                    "kind": "PackageDeclaration",
                    "name": {"kind": "Identifier", "text": "sample_pkg"},
                },
            ],
        },
    }],
}


class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = _init_project(Path(self.temp.name) / "demo")
        self.project = load_project(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_auto_uses_scanner_when_slang_is_missing(self):
        with patch("veriforge.source_index.shutil.which", return_value=None):
            result = build_source_index(self.project)
        self.assertEqual(result["backend"], "scanner")
        self.assertIsNone(result["parser"])
        self.assertEqual([item["status"] for item in result["sources"]], ["indexed", "indexed"])

    def test_explicit_slang_requires_executable(self):
        with patch("veriforge.source_index.shutil.which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "Slang executable"):
                build_source_index(self.project, "slang")

    def test_slang_parses_and_indexes_design_units(self):
        config = self.root / "veriforge.toml"
        config.write_text(config.read_text().replace(
            'standard = "1800-2017"',
            'standard = "1800-2017"\ninclude_dirs = ["include"]\ndefines = ["SIMULATION=1"]',
        ))
        self.project = load_project(self.root)
        successful = type("Result", (), {"returncode": 0, "stdout": json.dumps(_CST), "stderr": ""})()
        version = type("Result", (), {"returncode": 0, "stdout": "slang test version", "stderr": ""})()
        with (
            patch("veriforge.source_index.shutil.which", return_value="/tools/slang"),
            patch("veriforge.source_index.subprocess.run", side_effect=[successful, successful, version]) as run,
        ):
            result = build_source_index(self.project, "slang")
        self.assertEqual(result["parser"]["version"], "slang test version")
        self.assertEqual(result["backend"], "slang")
        self.assertEqual(result["sources"][0]["status"], "parsed")
        self.assertEqual(
            result["sources"][0]["design_units"],
            [{"kind": "module", "name": "sample_top"}, {"kind": "package", "name": "sample_pkg"}],
        )
        self.assertIn("--parse-only", run.call_args_list[1].args[0])
        self.assertIn("--cst-json", run.call_args_list[1].args[0])
        self.assertIn("--include-directory", run.call_args_list[1].args[0])
        self.assertIn("--define-macro", run.call_args_list[1].args[0])

    def test_slang_parse_errors_are_reported_per_source(self):
        version = type("Result", (), {"returncode": 0, "stdout": "slang", "stderr": ""})()
        failed = type("Result", (), {"returncode": 1, "stdout": "{}", "stderr": "syntax error"})()
        with (
            patch("veriforge.source_index.shutil.which", return_value="slang"),
            patch("veriforge.source_index.subprocess.run", side_effect=[failed, failed, version]),
        ):
            result = build_source_index(self.project, "slang")
        self.assertEqual([item["status"] for item in result["sources"]], ["parse_error", "parse_error"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assertTrue(all("syntax error" in item["diagnostic"] for item in result["sources"]))

    def test_index_config_validates_slang_standard(self):
        config = self.root / "veriforge.toml"
        config.write_text(config.read_text().replace('standard = "1800-2017"', 'standard = "broken"'))
        with self.assertRaisesRegex(ValueError, r"\[index\]\.standard"):
            load_project(self.root)


if __name__ == "__main__":
    unittest.main()
