"""阶段分离契约：准备零联系、发送零模型、预算上限、中断留存与显式续作。

使用真实 store、真实 runner 和真实 CLI；外部模型用 tests/fake_host.py 替身，
浏览器用离线替身并记录真实调用顺序。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import support

from legwork import config as config_module
from legwork import runner, sources
from legwork.simulated import SimulatedBrowser
from legwork.store import Store

NOW = "2026-03-02T10:00:00+08:00"
REPO = support.REPO


class RecordingBrowser(SimulatedBrowser):
    """离线浏览器 + 调用轨迹：证明准备阶段零联系、发送内容来自持久正文。"""

    def __init__(self, fixtures, events):
        super().__init__(fixtures)
        self.events = events

    def open_job(self, job):
        self.events.append(("open", job["job_id"]))
        return super().open_job(job)

    def click_contact(self, job):
        self.events.append(("contact", job["job_id"]))
        return super().click_contact(job)

    def click_send(self, text, identity):
        self.events.append(("chat_send", text))
        return super().click_send(text, identity)


def kinds(events):
    return [kind for kind, _ in events]


class PhaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="boss-phases-")))
        self.data = self.tmp / "data"
        self.config_path = support.write_config(
            self.tmp / "config.json", live=True, batch_size=1
        )
        self.fixtures = support.write_fixtures(self.tmp / "fixtures")
        self.marker = support.decoy_browser(self.tmp / "bin")
        self.host = support.write_fake_host(self.tmp / "fake")

    # -- 辅助 ---------------------------------------------------------------
    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "legwork", "--data-dir", str(self.data), *args],
            cwd=str(self.tmp), env=support.cli_env(self.tmp / "bin"),
            capture_output=True, text=True, timeout=180, check=False,
        )

    def run_cli(self, *extra, command=None):
        return self.cli(
            "run", "--config", str(self.config_path), "--fixtures", str(self.fixtures),
            "--now", NOW, "--host-command", str(command or self.host),
            "--workdir", str(self.tmp), *extra,
        )

    def status(self):
        proc = self.cli("status", "--config", str(self.config_path), "--now", NOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def store(self):
        store = Store(self.data / "state.sqlite3")
        self.addCleanup(store.close)
        return store

    def today(self):
        proc = self.cli("today", "--config", str(self.config_path), "--day", "2026-03-02")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def flaky_host(self):
        """第一次调用成功、之后失败：模拟第二批模型失败。"""
        counter = self.tmp / "flaky-calls"
        script = self.tmp / "flaky.py"
        script.write_text(
            "import os, sys\n"
            f"counter = {str(counter)!r}\n"
            "calls = int(open(counter).read()) if os.path.exists(counter) else 0\n"
            "open(counter, 'w').write(str(calls + 1))\n"
            "if calls >= 1:\n"
            "    sys.stderr.write('simulated second batch failure\\n')\n"
            "    sys.exit(3)\n"
            "os.execv(sys.executable, [sys.executable, "
            f"{str(REPO / 'tests' / 'fake_host.py')!r}" + "] + sys.argv[1:])\n",
            encoding="utf-8",
        )
        shim = self.tmp / "host-flaky"
        shim.write_text(
            f'#!/bin/sh\nexec {sys.executable} {script} "$@"\n', encoding="utf-8"
        )
        shim.chmod(0o755)
        return shim


class PrepareIsolationTest(PhaseCase):
    def test_d04_prepare_only_never_contacts_or_sends(self):
        proc = self.run_cli("--prepare-only")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "prepared", payload)
        self.assertEqual(payload["batches"], 4)
        self.assertEqual(payload["sent"], 0)
        self.assertFalse(self.marker.exists(), "准备阶段不能操作真实浏览器")
        status = self.status()
        self.assertEqual(status["jobs"]["ready"], 4)
        self.assertEqual(status["attempts"], [])

    def test_d04_second_batch_failure_keeps_first_batch_and_sends_nothing(self):
        proc = self.run_cli(command=self.flaky_host())
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "host_failed")
        self.assertEqual(payload["sent"], 0)
        self.assertFalse(self.marker.exists())
        store = self.store()
        self.assertEqual(_run(store, payload["run_id"])["prepare_state"], "failed")
        self.assertEqual(len(store.ready_jobs(payload["run_id"])), 1, "前面批次的结果要保留")
        self.assertEqual(store.quota_used(support.BASE_CONFIG["account_id"], "2026-03-02"), 0)

        resumed = self.run_cli("--resume-run", payload["run_id"])
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        resumed_payload = json.loads(resumed.stdout)
        self.assertEqual(resumed_payload["status"], "sent", resumed_payload)
        self.assertEqual(resumed_payload["sent"], 4)
        self.assertEqual(self.status()["jobs"]["ready"], 0)

    def test_finalize_is_required_before_manual_send(self):
        prepared = json.loads(self.run_cli("--prepare-only").stdout)
        # prepare-only 之后准备状态已是 prepared；新建一次准备再验证未结束时的拦截。
        support.write_config(self.tmp / "config2.json", live=True, batch_size=1, daily_limit=4)
        manual = self.cli(
            "prepare", "--config", str(self.tmp / "config2.json"),
            "--fixtures", str(self.fixtures), "--now", NOW,
        )
        self.assertEqual(manual.returncode, 0, manual.stderr)
        run_id = json.loads(manual.stdout)["run_id"]
        summary = json.loads(
            self.cli("packet", "--config", str(self.tmp / "config2.json"), "--run-id", run_id).stdout
        )
        document = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
        results = support.write_json(self.tmp / "results.json", support.model_results(document))
        decided = self.cli(
            "decide", "--config", str(self.tmp / "config2.json"), "--run-id", run_id,
            "--packet", summary["path"], "--results", str(results),
        )
        self.assertEqual(decided.returncode, 0, decided.stderr)
        blocked = self.cli(
            "send", "--config", str(self.tmp / "config2.json"), "--run-id", run_id,
            "--fixtures", str(self.fixtures), "--now", NOW,
        )
        self.assertEqual(blocked.returncode, 3, blocked.stdout)
        self.assertEqual(json.loads(blocked.stderr)["error"]["code"], "prepare_incomplete")
        self.assertFalse(self.marker.exists())
        finalized = self.cli(
            "finalize", "--config", str(self.tmp / "config2.json"), "--run-id", run_id
        )
        self.assertEqual(finalized.returncode, 3, finalized.stdout)
        self.assertEqual(json.loads(finalized.stderr)["error"]["code"], "prepare_incomplete")
        completed = self.cli(
            "run", "--config", str(self.tmp / "config2.json"), "--fixtures", str(self.fixtures),
            "--resume-run", run_id, "--prepare-only", "--host-command", str(self.host), "--now", NOW,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["prepared"], 4)
        ready = self.cli(
            "send", "--config", str(self.tmp / "config2.json"), "--run-id", run_id,
            "--fixtures", str(self.fixtures), "--now", NOW,
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertEqual(json.loads(ready.stdout)["sent"], 4)
        self.assertFalse(self.marker.exists())
        _ = prepared


def _run(store, run_id):
    run = store.run(run_id)
    assert run is not None
    return run


class SendIsolationTest(PhaseCase):
    def prepare_only(self):
        proc = self.run_cli("--prepare-only")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)["run_id"]

    def test_d05_send_uses_saved_body_and_never_calls_model(self):
        run_id = self.prepare_only()
        config = config_module.load(self.config_path)
        store = self.store()
        expected = {}
        for job in store.ready_jobs(run_id):
            decision = store.decision(run_id, job["job_id"])
            assert decision is not None
            expected[job["job_id"]] = decision["greeting"]
        self.assertEqual(len(expected), 4)
        events = []
        browser = RecordingBrowser(self.fixtures, events)

        def forbidden_model(*_args, **_kwargs):
            raise AssertionError("发送阶段不能调用模型")

        with patch("legwork.runner._invoke_model", side_effect=forbidden_model):
            outcome, code = runner.run_once(
                store, config, sources.FixtureSource(self.fixtures), data_dir=self.data,
                clock=lambda: datetime.fromisoformat(NOW), browser=browser,
                resume_run=run_id, host_command="must-not-run",
            )
        self.assertEqual(code, 0, outcome)
        self.assertEqual(outcome["sent"], 4)
        sent = [value for kind, value in events if kind == "chat_send"]
        self.assertEqual(sorted(sent), sorted(expected.values()), "发送正文必须与持久正文完全一致")
        self.assertEqual(kinds(events).count("contact"), 4)
        self.assertNotIn("model", kinds(events))


class BudgetTest(PhaseCase):
    def seed_attempts(self, start, stop):
        with Store(self.data / "state.sqlite3") as store:
            store.bind_account(support.BASE_CONFIG["account_id"])
            store.require_mode("fixtures")
            for index in range(start, stop):
                store.claim_attempt(
                    f"seed-{index}", support.BASE_CONFIG["account_id"], "seed-run",
                    f"seed-job-{index}", "2026-03-02", NOW,
                )

    def test_d08_prepare_budget_and_send_recheck(self):
        support.write_config(self.config_path, live=True, batch_size=3, daily_limit=10)
        self.seed_attempts(0, 7)
        proc = self.run_cli("--prepare-only")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["prepared"], 3, payload)
        self.assertEqual(payload["batches"], 1)
        self.assertEqual(self.status()["quota"]["used"], 7)
        self.assertIn("本轮额度不足，未安排判断", self.today())

        # 别处又占用两次：发送阶段必须按实际累计额度再核对，不能超发。
        self.seed_attempts(7, 9)
        resumed = self.run_cli("--resume-run", payload["run_id"])
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        resumed_payload = json.loads(resumed.stdout)
        self.assertEqual(resumed_payload["sent"], 1, resumed_payload)
        self.assertEqual(resumed_payload["batches"], 0, "额度不足不能再调用模型")
        self.assertEqual(self.status()["quota"]["used"], 10)

    def test_d08_full_quota_never_calls_model(self):
        support.write_config(self.config_path, live=True, batch_size=1, daily_limit=5)
        self.seed_attempts(0, 5)
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "daily_limit")
        self.assertEqual(json.loads(proc.stdout)["sent"], 0)
        self.assertFalse((self.tmp / "flaky-calls").exists())
        self.assertFalse(self.marker.exists())


class AutoChainTest(PhaseCase):
    def test_d14_prepares_all_batches_before_any_send(self):
        support.write_config(self.config_path, live=True, batch_size=2, daily_limit=5)
        config = config_module.load(self.config_path)
        events = []
        browser = RecordingBrowser(self.fixtures, events)
        original = runner._invoke_model

        def recording(*args, **kwargs):
            events.append(("model", ""))
            return original(*args, **kwargs)

        store = self.store()
        with patch("legwork.runner._invoke_model", side_effect=recording):
            outcome, code = runner.run_once(
                store, config, sources.FixtureSource(self.fixtures), data_dir=self.data,
                clock=lambda: datetime.fromisoformat(NOW), browser=browser,
                host_command=str(self.host), workdir=str(self.tmp),
            )
        self.assertEqual(code, 0, outcome)
        self.assertEqual(outcome["status"], "sent", outcome)
        self.assertGreaterEqual(outcome["batches"], 2)
        trace = kinds(events)
        self.assertEqual(trace.count("model"), outcome["batches"])
        first_contact = trace.index("contact")
        self.assertLess(trace.index("model"), first_contact, "准备阶段必须先于发送")
        self.assertNotIn("model", trace[first_contact:], "发送阶段不能调用模型")
        self.assertEqual(outcome["sent"], 4)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
