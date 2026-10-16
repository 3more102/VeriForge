import tempfile
import unittest
from pathlib import Path

from veriforge.cli import _init_project, main
from veriforge.project import ProjectError, load_project


class ProjectTests(unittest.TestCase):
    def test_init_creates_loadable_demo_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            project = load_project(root)
            self.assertEqual(project.name, "demo")
            self.assertEqual(project.simulator, "verilator")
            self.assertEqual(project.waveform_file, root / "waveform.vcd")
            self.assertEqual(len(project.requirements), 2)
            self.assertEqual(len(project.sources), 2)

    def test_refuses_to_overwrite_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep.txt").write_text("keep")
            with self.assertRaises(ProjectError):
                _init_project(root)
            self.assertEqual((root / "keep.txt").read_text(), "keep")

    def test_rejects_unknown_required_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            manifest = root / "requirements.csv"
            manifest.write_text(
                "id,description,priority,area,required_evidence\nR1,Needs a test,high,unit,ai\n"
            )
            with self.assertRaisesRegex(ProjectError, "unsupported evidence"):
                load_project(root)

    def test_add_command_copies_source_and_updates_manifest(self):
        from veriforge.cli import main

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = _init_project(base / "demo")
            source = base / "new_block.sv"
            source.write_text("module new_block; endmodule\n")
            self.assertEqual(
                main(["add", "rtl", str(source), "--project", str(root)]), 0
            )
            project = load_project(root)
            self.assertEqual(len(project.sources), 3)
            self.assertEqual(project.sources[-1], root / "rtl/new_block.sv")

    def test_configure_command_selects_icarus(self):
        from veriforge.cli import main

        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            self.assertEqual(main(["configure", "--simulator", "icarus", str(root)]), 0)
            project = load_project(root)
            self.assertEqual(project.simulator, "icarus")

    def test_configure_accepts_commercial_adapter_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            self.assertEqual(main(["configure", "--simulator", "xcelium", str(root)]), 0)
            self.assertEqual(load_project(root).simulator, "xcelium")

    def test_rejects_unsupported_simulator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            config = root / "veriforge.toml"
            config.write_text(config.read_text().replace('name = "verilator"', 'name = "unknown"'))
            with self.assertRaisesRegex(ProjectError, "choose one of"):
                load_project(root)

    def test_rejects_invalid_top_module_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = _init_project(Path(directory) / "demo")
            config = root / "veriforge.toml"
            config.write_text(config.read_text().replace('top_module = "tb_top"', 'top_module = "../tb_top"'))
            with self.assertRaisesRegex(ProjectError, "SystemVerilog identifier"):
                load_project(root)


if __name__ == "__main__":
    unittest.main()
