"""tools/run-real.sh：只放行會用到 key 的子命令，而且 uv 的參數固定，使用者的參數只會交給 translation_eval。

用一個假的 uv 放在 PATH 最前面，記下它收到的參數；不讀 .env，也不連網。
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "run-real.sh"

FAKE_UV = """#!/bin/sh
for arg in "$@"; do printf '%s\\n' "$arg"; done > "$FAKE_UV_LOG"
"""


class RunRealTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin = Path(tmp.name)
        self.log = self.bin / "uv-args.txt"
        uv = self.bin / "uv"
        uv.write_text(FAKE_UV)
        uv.chmod(uv.stat().st_mode | stat.S_IEXEC)

    def run_script(self, *args):
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "FAKE_UV_LOG": str(self.log)}
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def test_key_using_subcommands_run_translation_eval_through_uv(self):
        for command in ("translate", "judge", "calibrate", "crosscheck", "providers"):
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                result = self.run_script(command, "--config", "configs/experiments/x.json", "--limit", "1")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.log.read_text().split("\n")[:-1],
                    ["run", "--locked", "--env-file", ".env", "python", "-I", "-m", "translation_eval",
                     command, "--config", "configs/experiments/x.json", "--limit", "1"])

    def test_anything_else_is_refused_before_uv_runs(self):
        for args in ([], ["report"], ["bash"], ["python", "-c", "pass"], ["--env-file", "other"], ["env"], [""]):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("用法", result.stderr)
                self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
