import tempfile
import unittest
from pathlib import Path

from veriforge.cli import _init_project
from veriforge.project import load_project
from veriforge.scanner import scan_sources


class ScannerTests(unittest.TestCase):
    def test_ignores_nonmatching_and_unknown_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            source = root / "tb/tb_top.sv"
            source.write_text('''module tb_top;
// vf:asserts REQ-DOES-NOT-EXIST
assert (1);
endmodule
''')
            evidence = scan_sources(load_project(root))
            self.assertTrue(any("unknown requirement" in warning for warning in evidence.warnings))
            self.assertEqual(evidence.assertions, {})

    def test_rejects_annotation_not_followed_by_matching_statement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            source = root / "tb/tb_top.sv"
            source.write_text('''module tb_top;
// vf:asserts REQ-RV-001
logic value;
endmodule
''')
            evidence = scan_sources(load_project(root))
            self.assertTrue(any("not followed" in warning for warning in evidence.warnings))


if __name__ == "__main__":
    unittest.main()
