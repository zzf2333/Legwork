"""按真实 CLI 入口验证单次宿主导入：空批次不调模型、参数安全边界、失败不发送、可续作。"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import support

NOW = "2026-03-02T10:00:00+08:00"
FORBIDDEN = (
    "--skip-git-repo-check",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--ignore-user-config",
    "--ignore-rules",
    "--approve-for-me",
)


class RunnerCase(unittest.TestCase):
    config_overrides: ClassVar[dict] = {"live": True}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="legwork-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = self.tmp / "data"
        self.config = support.write_config(self.tmp / "config.json", **self.config_overrides)
        self.fixtures = support.write_fixtures(self.tmp / "fixtures")
        self.marker = support.decoy_browser(self.tmp / "bin")
        self.host = support.write_fake_host(self.tmp / "fake")
        self.argv_log = self.tmp / "host-argv.json"

    def run_once(self, *extra, env=None, command=None):
        environment = dict(os.environ)
        environment["FAKE_HOST_ARGV"] = str(self.argv_log)
        environment.update(env or {})
        proc = subprocess.run(
            [sys_executable(), "-m", "legwork", "--data-dir", str(self.data), "run",
             "--config", str(self.config), "--fixtures", str(self.fixtures), "--now", NOW,
             "--host-command", str(command or self.host), *extra],
            cwd=str(self.tmp),
            env={**support.cli_env(self.tmp / "bin"), **environment},
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        return proc

    def status(self):
        proc = support.run_cli(self.tmp, self.data, "status", "--config", str(self.config), "--now", NOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)


def sys_executable():
    import sys

    return sys.executable


class RunnerTest(RunnerCase):
    def test_empty_packet_never_calls_model(self):
        support.write_fixtures(self.tmp / "fixtures", contacts=support.CONTACTS, jobs=[])
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "no_candidates")
        self.assertFalse(self.argv_log.exists(), "空批次不应启动模型")
        self.assertFalse(self.marker.exists())

    def test_model_arguments_stay_read_only_and_results_are_sent(self):
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "sent")
        self.assertEqual(payload["sent"], 4)
        self.assertFalse(self.marker.exists())
        argv = json.loads(self.argv_log.read_text(encoding="utf-8"))
        self.assertEqual(argv[0], "exec")
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", argv)
        schema = Path(argv[argv.index("--output-schema") + 1])
        self.assertTrue(schema.is_file())
        self.assertIn("--output-last-message", argv)
        for flag in FORBIDDEN:
            self.assertNotIn(flag, argv)
        self.assertNotIn("--full-auto", argv)

    def test_host_failure_stops_before_send_and_run_resumes(self):
        failed = self.run_once(env={"FAKE_CODEX_FAIL": "1"})
        self.assertEqual(failed.returncode, 4, failed.stdout + failed.stderr)
        payload = json.loads(failed.stdout)
        run_id = payload["run_id"]
        self.assertEqual(payload["status"], "host_failed")
        self.assertFalse(self.marker.exists())
        status = self.status()
        self.assertEqual(status["jobs"]["awaiting_decisions"], 4)
        self.assertEqual(status["jobs"]["sent"], 0)

        resumed = self.run_once("--resume-run", run_id)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        resumed_payload = json.loads(resumed.stdout)
        self.assertEqual(resumed_payload["run_id"], run_id)
        self.assertEqual(resumed_payload["status"], "sent")
        self.assertEqual(self.status()["jobs"]["sent"], 4)

    def test_invalid_model_batch_is_rejected_without_send(self):
        proc = self.run_once(env={"FAKE_CODEX_MODE": "unmet"})
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "decisions_rejected")
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.status()["jobs"]["sent"], 0)

    def test_live_disabled_keeps_ready_without_send(self):
        support.write_config(self.config, live=False)
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "ready_live_disabled")
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
