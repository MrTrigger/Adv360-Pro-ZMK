"""Compare generated macro bytes with the existing firmware version caller."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("version", ROOT / "scripts/bazel/version.py")
version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version)


class VersionTests(unittest.TestCase):
    def test_matches_existing_caller_for_legacy_and_studio(self):
        for flavor in (".", "clique"):
            with self.subTest(flavor=flavor), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "config").mkdir()
                tools = root / "bin"
                tools.mkdir()
                (tools / "date").write_text("#!/bin/sh\necho 20261025\n")
                (tools / "date").chmod(0o755)
                environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"])
                subprocess.run(["bash", str(ROOT / "scripts/tests/fixtures/legacy-version.sh"), "V3.0", "abc1234", flavor],
                               cwd=root, env=environment, check=True, stdout=subprocess.DEVNULL)
                self.assertEqual(version.render("20261025", "V3.0", "abc1234", flavor),
                                 (root / "config/version.dtsi").read_text())

    def test_cli_writes_only_the_requested_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            original = root / "config/version.dtsi"
            original.write_text("keep the checkout unchanged")
            destination = root / "declared/version.dtsi"
            subprocess.run(["python3", str(ROOT / "scripts/bazel/version.py"), "--date", "20261025",
                            "--branch", "V3.0", "--commit", "abc1234", "--output", str(destination)],
                           cwd=root, check=True)
            self.assertEqual(original.read_text(), "keep the checkout unchanged")
            self.assertIn("<&kp DOT>", destination.read_text())
            self.assertIn("<&kp MINUS>", destination.read_text())
            self.assertTrue(destination.read_text().endswith("<&kp RET>;\n};\n"))

    def test_invalid_date_does_not_replace_an_output(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "version.dtsi"
            destination.write_text("previous")
            run = subprocess.run(["python3", str(ROOT / "scripts/bazel/version.py"), "--date", "20260230",
                                  "--branch", "V3.0", "--commit", "abc1234", "--output", str(destination)],
                                 capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(destination.read_text(), "previous")


if __name__ == "__main__":
    unittest.main()
