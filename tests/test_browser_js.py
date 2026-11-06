"""在独立 Node 上下文执行随包 JS，验证读取函数作用域与身份切换（不接触真实浏览器）。"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "tests" / "js" / "guard_contract.mjs"
LOCATE_HARNESS = REPO / "tests" / "js" / "locate_contract.mjs"


def _run_node(harness: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(harness)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


class GuardContractTest(unittest.TestCase):
    @unittest.skipIf(shutil.which("node") is None, "本机没有 node，无法执行 JS 契约检查")
    def test_guard_runs_with_injected_reader_and_rejects_identity_switch(self):
        proc = _run_node(HARNESS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("guard-contract-ok", proc.stdout)

    @unittest.skipIf(shutil.which("node") is None, "本机没有 node，无法执行 JS 契约检查")
    def test_locate_scrolls_virtual_list_and_rejects_ambiguous_target(self):
        proc = _run_node(LOCATE_HARNESS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("locate-contract-ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
