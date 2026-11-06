"""验证本地互斥：同一数据目录不允许多个进程同时操作同一浏览器。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import support

from legwork import ego

NOW = "2026-03-02T10:00:00+08:00"


class BrowserLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="legwork-lock-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = self.tmp / "data"
        self.config = support.write_config(self.tmp / "config.json", live=True, batch_size=4)
        self.fixtures = support.write_fixtures(self.tmp / "fixtures")
        self.marker = support.decoy_browser(self.tmp / "bin")

    def test_second_sender_is_refused_while_lock_is_held(self):
        prepared = support.run_cli(
            self.tmp, self.data, "prepare", "--config", str(self.config),
            "--fixtures", str(self.fixtures), "--now", NOW,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        run_id = json.loads(prepared.stdout)["run_id"]
        summary = json.loads(
            support.run_cli(
                self.tmp, self.data, "packet", "--config", str(self.config), "--run-id", run_id
            ).stdout
        )
        packet = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
        results = support.write_json(self.tmp / "results.json", support.model_results(packet))
        decided = support.run_cli(
            self.tmp, self.data, "decide", "--config", str(self.config), "--run-id", run_id,
            "--packet", summary["path"], "--results", str(results),
        )
        self.assertEqual(decided.returncode, 0, decided.stderr)
        finalized = support.run_cli(
            self.tmp, self.data, "finalize", "--config", str(self.config), "--run-id", run_id
        )
        self.assertEqual(finalized.returncode, 0, finalized.stderr)

        with ego.browser_lock(self.data, timeout=0):
            blocked = support.run_cli(
                self.tmp, self.data, "send", "--run-id", run_id,
                "--config", str(self.config), "--fixtures", str(self.fixtures), "--now", NOW,
            )
        self.assertEqual(blocked.returncode, 3, blocked.stdout + blocked.stderr)
        self.assertEqual(json.loads(blocked.stderr)["error"]["code"], "browser_busy")
        self.assertEqual(
            json.loads(support.run_cli(self.tmp, self.data, "status").stdout)["jobs"]["sent"], 0
        )


if __name__ == "__main__":
    unittest.main()
