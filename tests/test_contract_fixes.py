"""独立审查第 1 轮 P1 的契约回归：来源隔离、输入绑定、严格 schema、停止与续作、浏览器守卫。"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import support

from legwork import config as config_module
from legwork import ego, sending
from legwork.ego import EgoBrowser
from legwork.simulated import SimulatedBrowser
from legwork.store import Store, StoreError

REPO = Path(__file__).resolve().parent.parent
NOW_DAY1 = "2026-03-02T10:00:00+08:00"
NOW_DAY2 = "2026-03-03T10:00:00+08:00"


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.tmp / "data"
        self.fixtures = support.write_fixtures(self.tmp / "fixtures")

    def cli(self, *argv):
        return support.run_cli(self.tmp, self.data, *argv, path_prefix=self.tmp)

    def config(self, **overrides):
        return support.write_config(self.tmp / "config.json", **overrides)

    def prepare(self, config, *extra, now=NOW_DAY1):
        proc = self.cli("prepare", "--config", str(config), "--now", now, *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def packet(self, config, run_id, *extra):
        proc = self.cli("packet", "--config", str(config), "--run-id", run_id, *extra)
        path = json.loads(proc.stdout)["path"]
        return Path(path), json.loads(Path(path).read_text(encoding="utf-8"))

    def decide(self, config, run_id, packet_path, results, *, now=NOW_DAY1):
        results_path = self.tmp / "results.json"
        results_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        proc = self.cli(
            "decide", "--config", str(config), "--run-id", run_id,
            "--packet", str(packet_path), "--results", str(results_path), "--now", now,
        )
        # 旧回归只发送首批；剩余候选显式排除，不能用finalize跳过未完成准备。
        if proc.returncode == 0:
            while True:
                summary = json.loads(self.cli("packet", "--config", str(config), "--run-id", run_id).stdout)
                if summary["path"] is None:
                    break
                document = json.loads(Path(summary["path"]).read_text())
                results_path.write_text(json.dumps(support.model_results(document, action="skip")))
                skipped = self.cli("decide", "--config", str(config), "--run-id", run_id,
                                   "--packet", summary["path"], "--results", str(results_path), "--now", now)
                self.assertEqual(skipped.returncode, 0, skipped.stderr)
            self.finalize(config, run_id, now=now)
        return proc

    def finalize(self, config, run_id, *, now=NOW_DAY1):
        proc = self.cli(
            "finalize", "--config", str(config), "--run-id", run_id, "--now", now
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc


class SourceBindingTest(Harness):
    def test_fixture_run_is_refused_by_live_send(self):
        config = self.config(live=True)
        run = self.prepare(config, "--fixtures", str(self.fixtures))
        packet_path, document = self.packet(config, run["run_id"])
        self.assertEqual(self.decide(config, run["run_id"], packet_path, support.model_results(document)).returncode, 0)
        proc = self.cli("send", "--config", str(config), "--run-id", run["run_id"])
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn(json.loads(proc.stderr)["error"]["code"], {"source_mismatch", "store_error"})
        self.assertFalse((self.tmp / "ego-browser-called").exists())

    def test_data_dir_is_bound_to_one_channel(self):
        config = self.config(live=True)
        self.prepare(config, "--fixtures", str(self.fixtures))
        proc = self.cli("prepare", "--config", str(config))
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "store_error")
        self.assertFalse((self.tmp / "ego-browser-called").exists())

    def test_simulated_attempts_do_not_consume_live_quota(self):
        account = support.BASE_CONFIG["account_id"]
        with Store(self.data / "state.sqlite3") as store:
            store.bind_account(account)
            store.require_mode("fixtures")
            store.claim_attempt("a1", account, "run-x", "job-a1", "2026-03-02", NOW_DAY1)
        with Store(self.data / "state.sqlite3") as store:
            store.bind_account(account)
            self.assertEqual(store.quota_used(account, "2026-03-02"), 1)
            with self.assertRaises(StoreError):
                store.require_mode("live")
        other = self.tmp / "live-data"
        with Store(other / "state.sqlite3") as store:
            store.bind_account(account)
            self.assertEqual(store.require_mode("live"), "live")
            self.assertEqual(store.quota_used(account, "2026-03-02"), 0)
            self.assertIsNone(store.attempt(account, "job-a1"))


class PacketBindingTest(Harness):
    def test_tampered_packet_is_rejected(self):
        config = self.config()
        run = self.prepare(config, "--fixtures", str(self.fixtures))
        packet_path, document = self.packet(config, run["run_id"])
        results = support.model_results(document)
        document["jobs"][0]["description"] += "（被篡改）"
        document["resume_facts"][0]["text"] = "被篡改的简历事实"
        packet_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        proc = self.decide(config, run["run_id"], packet_path, results)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "packet_input_mismatch")


class StrictSchemaTest(unittest.TestCase):
    def test_every_property_is_required(self):
        schema = json.loads(
            (REPO / "legwork/resources/schema/model_result.schema.json").read_text(encoding="utf-8")
        )
        nodes = {"root": schema} | schema["$defs"]
        for name, node in nodes.items():
            if node.get("type") != "object":
                continue
            missing = sorted(set(node["properties"]) - set(node.get("required") or []))
            self.assertEqual(missing, [], f"{name} 未把属性列入 required")


class SkipShapeTest(Harness):
    def test_skip_uses_empty_greeting_and_facts(self):
        config = self.config()
        run = self.prepare(config, "--fixtures", str(self.fixtures))
        packet_path, document = self.packet(config, run["run_id"])
        results = support.model_results(document, action="skip")
        for item in results["results"]:
            item["greeting"] = ""
            item["resume_fact_ids"] = []
        proc = self.decide(config, run["run_id"], packet_path, results)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["skipped"], 3)


class FixedNowTest(Harness):
    def test_live_channel_refuses_fixed_now(self):
        config = self.config(live=True)
        proc = self.cli("send", "--config", str(config), "--run-id", "whatever", "--now", NOW_DAY1)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "fixed_now_forbidden")


class SendStopTest(Harness):
    def test_uncertain_stops_round_and_leaves_rest_ready(self):
        config = self.config(live=True)
        browser = json.loads((self.fixtures / "browser.json").read_text(encoding="utf-8"))
        browser["jobs"]["job-a1"] = {"default_message": "您好", "fail_at": "click_send"}
        support.write_json(self.fixtures / "browser.json", browser)
        run = self.prepare(config, "--fixtures", str(self.fixtures))
        packet_path, document = self.packet(config, run["run_id"])
        self.assertEqual(self.decide(config, run["run_id"], packet_path, support.model_results(document)).returncode, 0)
        proc = self.cli(
            "send", "--config", str(config), "--run-id", run["run_id"],
            "--fixtures", str(self.fixtures), "--now", NOW_DAY1,
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 4, proc.stdout)
        self.assertEqual([item["job_id"] for item in payload["attempts"]], ["job-a1"])
        self.assertEqual(payload["stopped"], "uncertain")

    def test_day_rollover_stops_before_next_claim(self):
        config = self.config(live=True)
        run = self.prepare(config, "--fixtures", str(self.fixtures))
        packet_path, document = self.packet(config, run["run_id"])
        self.assertEqual(self.decide(config, run["run_id"], packet_path, support.model_results(document)).returncode, 0)
        zone = ZoneInfo(support.BASE_CONFIG["timezone"])
        start = datetime.fromisoformat(NOW_DAY1).astimezone(zone)
        moments = [start, start, start + timedelta(days=1)]
        with Store(self.data / "state.sqlite3") as store:
            store.bind_account(support.BASE_CONFIG["account_id"])
            store.require_mode("fixtures")
            payload, code = sending.send_ready(
                store,
                config_module.load(config),
                store.run(run["run_id"]),
                SimulatedBrowser(self.fixtures),
                clock=lambda: moments.pop(0),
                data_dir=self.data,
            )
        self.assertEqual(code, 4, payload)
        self.assertEqual(payload["sent"], 1)
        self.assertEqual(payload["stopped"], "day_rolled")
        self.assertEqual(payload["attempted"], 1)


class ResumeTest(Harness):
    def test_resume_sends_ready_without_calling_model(self):
        config = self.config(live=True, batch_size=4)
        run = self.prepare(config, "--fixtures", str(self.fixtures))
        packet_path, document = self.packet(config, run["run_id"])
        self.assertEqual(self.decide(config, run["run_id"], packet_path, support.model_results(document)).returncode, 0)
        proc = self.cli(
            "run", "--config", str(config), "--fixtures", str(self.fixtures),
            "--resume-run", run["run_id"], "--host-command", str(self.tmp / "absent-host"),
            "--workdir", str(self.tmp), "--now", NOW_DAY1,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "sent")
        self.assertEqual(payload["sent"], 4)

    def test_run_completes_more_than_one_batch(self):
        config = self.config(live=True)
        model = support.write_fake_host(self.tmp)
        proc = self.cli(
            "run", "--config", str(config), "--fixtures", str(self.fixtures),
            "--host-command", str(model), "--workdir", str(self.tmp), "--now", NOW_DAY1,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["sent"], 4, payload)
        self.assertGreaterEqual(payload["batches"], 2)


class StaleReadyTest(Harness):
    def test_expired_ready_does_not_block_new_prepare(self):
        config = self.config(live=True)
        run = self.prepare(config, "--fixtures", str(self.fixtures))
        packet_path, document = self.packet(config, run["run_id"])
        self.assertEqual(self.decide(config, run["run_id"], packet_path, support.model_results(document)).returncode, 0)
        again = self.prepare(config, "--fixtures", str(self.fixtures), now=NOW_DAY2)
        rejected = {item["job_id"]: item["code"] for item in again["rejected"]}
        self.assertNotEqual(rejected.get("job-a1"), "prior_run")
        self.assertGreaterEqual(again["accepted"], 3)


class BrowserContractTest(unittest.TestCase):
    def test_open_job_stays_on_existing_list(self):
        browser = EgoBrowser.__new__(EgoBrowser)
        browser.space = "p2"
        browser.job_page = "p1"
        browser.chat_page = "p1"
        browser.platform_user_id = "100000001"
        browser.binary = "echo"
        browser.timeout = 5.0
        browser._state_js = ego.resource_text("js/ego_page_state.js")
        browser._guard_js = ego.resource_text("js/ego_click_guard.js")
        browser._collect_js = ego.resource_text("js/ego_collect.js")
        captured = {}

        def fake_run(action, *, body, **arguments):
            captured["body"] = body
            return {}

        browser._run = fake_run
        browser.open_job({"job_id": "job-a1", "company": "示例", "boss_id": "boss-01"})
        self.assertNotIn("job.goto(", captured["body"])
        self.assertIn("li.job-card-box", captured["body"])
        self.assertIn("link.click()", captured["body"])


if __name__ == "__main__":
    unittest.main()
