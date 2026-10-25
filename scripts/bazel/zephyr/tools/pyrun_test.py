import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYRUN = Path(__file__).resolve().parent / "pyrun.py"


class PyRunTests(unittest.TestCase):
    def test_script_sees_main_argv_and_sibling_imports(self):
        directory = Path(tempfile.mkdtemp(prefix="pyrun-"))
        (directory / "helper.py").write_text("VALUE = 'from-sibling'\n")
        (directory / "tool.py").write_text(
            "import sys\nimport helper\nif __name__ == '__main__':\n    print(sys.argv, helper.VALUE)\n")
        result = subprocess.run([sys.executable, "-I", str(PYRUN), str(directory / "tool.py"), "--flag", "x"],
                                capture_output=True, text=True, cwd="/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"['{directory / 'tool.py'}', '--flag', 'x'] from-sibling")

    def test_cmake_style_probes_c_m_and_version(self):
        def run(*args):
            return subprocess.run([sys.executable, "-I", str(PYRUN), *args], capture_output=True, text=True, cwd="/tmp")
        probe = run("-c", "import sys; print(sys.argv, sys.version_info[:2] >= (3, 8))", "extra")
        self.assertEqual(probe.stdout.strip(), "['-c', 'extra'] True")
        self.assertEqual(run("-m", "json.tool", "--help").returncode, 0)
        self.assertRegex(run("--version").stdout, r"^Python 3\.\d+\.\d+")

    def test_exit_status_propagates(self):
        directory = Path(tempfile.mkdtemp(prefix="pyrun-"))
        (directory / "fail.py").write_text("raise SystemExit(7)\n")
        self.assertEqual(subprocess.run([sys.executable, "-I", str(PYRUN), str(directory / "fail.py")]).returncode, 7)


if __name__ == "__main__":
    unittest.main()
