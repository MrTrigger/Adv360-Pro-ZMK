"""Exercise the firmware caller without invoking Docker or a compiler."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FirmwareCallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "config").mkdir()
        (self.root / "config/version.dtsi").write_text("original keyboard version")
        shutil.copyfile(ROOT / "scripts/build-firmware", self.root / "scripts/build-firmware")
        builder = self.root / "scripts/bzl"
        builder.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
Path("invocation.json").write_text(json.dumps(dict(args=sys.argv[1:],paths=json.loads(os.environ["ADV_BAZEL_EXPORT_PATHS"]))))
if os.environ.get("FAIL_BUILD"):sys.exit(7)
for relative in json.loads(os.environ["ADV_BAZEL_EXPORT_PATHS"]):
 if os.environ.get("OMIT_UF2") and relative.endswith(".uf2"):continue
 p=Path(os.environ["ADV_BAZEL_STATE"])/"artifacts"/relative
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text("actual "+relative)
''')
        builder.chmod(0o755)

    def run_caller(self, *arguments, **environment):
        env = {key: value for key, value in os.environ.items() if not key.startswith("ADV_BAZEL_") and key not in ["TIMESTAMP", "COMMIT"]}
        return subprocess.run(["python3", str(self.root / "scripts/build-firmware"), "--timestamp", "202610250210",
                               "--date", "20261025", "--branch", "V3.0", "--commit", "abc1234", *arguments],
                              cwd=self.root, env=dict(env, **environment), text=True, capture_output=True)

    def test_default_builds_all_four_variants_and_preserves_uf2_names(self):
        run = self.run_caller()
        self.assertEqual(run.returncode, 0, run.stderr)
        invocation = json.loads((self.root / "invocation.json").read_text())
        self.assertEqual(invocation["args"][:5], ["build", "//firmware:left_legacy", "//firmware:right_legacy", "//firmware:left_studio", "//firmware:right_studio"])
        self.assertEqual(len(invocation["paths"]), 12)
        self.assertEqual((self.root / "firmware/202610250210-abc1234-left.uf2").read_text(),
                         "actual firmware/native/left_legacy/zephyr/zmk.uf2")
        self.assertEqual((self.root / "firmware/202610250210-abc1234-right-clique.uf2").read_text(),
                         "actual firmware/native/right_studio/zephyr/zmk.uf2")
        self.assertEqual((self.root / "config/version.dtsi").read_text(), "original keyboard version")

    def test_left_studio_builds_only_the_requested_variant(self):
        run = self.run_caller("--side", "left", "--flavor", "studio")
        self.assertEqual(run.returncode, 0, run.stderr)
        invocation = json.loads((self.root / "invocation.json").read_text())
        self.assertEqual(invocation["args"][1], "//firmware:left_studio")
        self.assertEqual(len(invocation["paths"]), 3)
        self.assertEqual([p.name for p in (self.root / "firmware").glob("*.uf2")], ["202610250210-abc1234-left-clique.uf2"])

    def test_failed_build_keeps_the_previous_firmware(self):
        previous = self.root / "firmware/previous.uf2"
        previous.parent.mkdir()
        previous.write_text("known working firmware")
        run = self.run_caller(FAIL_BUILD="1")
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(previous.read_text(), "known working firmware")
        self.assertFalse((self.root / "build").exists())

    def test_missing_export_publishes_no_firmware(self):
        run = self.run_caller(OMIT_UF2="1")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Missing regular native firmware export", run.stderr)
        self.assertFalse((self.root / "firmware").exists())

    def test_invalid_date_or_commit_is_refused_before_building(self):
        for arguments in [("--date", "20260230"), ("--commit", "../outside")]:
            with self.subTest(arguments=arguments):
                run = self.run_caller(*arguments)
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse((self.root / "invocation.json").exists())


if __name__ == "__main__":
    unittest.main()
