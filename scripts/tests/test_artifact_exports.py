"""Exercise named exports and caller materialization without Docker or Bazel."""

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "scripts/bazel/export-artifacts"


class ArtifactExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.target = self.root / "artifacts"

    def invoke(self, paths, command=()):
        return subprocess.run([sys.executable, str(EXPORT), str(self.bin), str(self.target),
                               json.dumps(paths), *command], capture_output=True, text=True)

    def test_named_files_from_symlinked_bazel_bin_without_runfiles(self):
        (self.bin / "app").mkdir()
        binary = self.bin / "app/firmware.elf"
        binary.write_bytes(b"firmware")
        binary.chmod(0o755)
        (self.bin / "tool.runfiles").symlink_to(self.root / "missing")
        alias = self.root / "bazel-bin"
        alias.symlink_to(self.bin, target_is_directory=True)
        self.bin = alias
        result = self.invoke(["app/firmware.elf"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / "app/firmware.elf").read_bytes(), b"firmware")
        self.assertEqual((self.target / "app/firmware.elf").stat().st_mode & 0o777, 0o755)
        self.assertEqual([p.relative_to(self.target).as_posix() for p in self.target.rglob("*")],
                         ["app", "app/firmware.elf"])

    def test_missing_input_preserves_all_previous_files(self):
        self.target.mkdir()
        (self.target / "first").write_text("previous")
        (self.bin / "first").write_text("replacement")
        result = self.invoke(["first", "missing"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.target / "first").read_text(), "previous")
        self.assertEqual([p.name for p in self.target.iterdir()], ["first"])

    def test_invalid_paths_are_rejected_before_building(self):
        marker = self.root / "ran"
        command = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)]
        for paths in ([], ["."], ["../outside"], ["/outside"], ["app/../outside"],
                      ["app//file"], ["file/"], ["file", "file"], "file", [123]):
            with self.subTest(paths=paths):
                self.assertNotEqual(self.invoke(paths, command).returncode, 0)
                self.assertFalse(marker.exists())

    def test_directories_and_escaping_source_links_are_rejected(self):
        (self.bin / "directory").mkdir()
        (self.root / "outside").write_text("outside")
        (self.bin / "escape").symlink_to(self.root / "outside")
        for name in ("directory", "escape"):
            with self.subTest(name=name):
                self.assertNotEqual(self.invoke([name]).returncode, 0)
                self.assertFalse((self.target / name).exists())

    def test_destination_parent_link_cannot_escape(self):
        (self.bin / "app").mkdir()
        (self.bin / "app/file").write_text("new")
        self.target.mkdir()
        (self.target / "app").symlink_to(self.root)
        self.assertNotEqual(self.invoke(["app/file"]).returncode, 0)
        self.assertFalse((self.root / "file").exists())

    def test_failed_build_does_not_export(self):
        (self.bin / "file").write_text("new")
        result = self.invoke(["file"], [sys.executable, "-c", "raise SystemExit(7)"])
        self.assertEqual(result.returncode, 7)
        self.assertFalse(self.target.exists())



if __name__ == "__main__":
    unittest.main()
