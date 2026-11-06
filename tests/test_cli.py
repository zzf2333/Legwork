"""按真实 CLI 入口验证首版契约：配置、准备、导出、决策、发送、状态。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import support

NOW_DAY1 = "2026-03-02T10:00:00+08:00"
NOW_DAY2 = "2026-03-03T10:00:00+08:00"


class CliCase(unittest.TestCase):
    config_overrides: ClassVar[dict] = {}
    browser_overrides = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="legwork-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = self.tmp / "data"
        self.config = support.write_config(self.tmp / "config.json", **self.config_overrides)
        self.fixtures = support.write_fixtures(
            self.tmp / "fixtures", browser=support.browser_script(self.browser_overrides)
        )
        self.marker = support.decoy_browser(self.tmp / "bin")

    def cli(self, *args):
        return support.run_cli(self.tmp, self.data, *args, path_prefix=self.tmp / "bin")

    def ok(self, *args):
        proc = self.cli(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return json.loads(proc.stdout)

    def fails(self, code, *args):
        proc = self.cli(*args)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        return json.loads(proc.stderr)["error"]

    def prepare(self, now=NOW_DAY1):
        return self.ok(
            "prepare",
            "--config",
            str(self.config),
            "--fixtures",
            str(self.fixtures),
            "--now",
            now,
        )

    def packet(self, run_id):
        summary = self.ok("packet", "--config", str(self.config), "--run-id", run_id)
        if not summary.get("path"):
            return summary
        document = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
        return document | {"path": summary["path"], "remaining": summary["remaining"]}

    def decide(self, run_id, packet, results):
        results_path = support.write_json(self.tmp / "results.json", results)
        return self.cli(
            "decide",
            "--config",
            str(self.config),
            "--run-id",
            run_id,
            "--packet",
            packet["path"],
            "--results",
            str(results_path),
        )

    def send(self, run_id, *extra, now=NOW_DAY1):
        return self.cli(
            "send",
            "--run-id",
            run_id,
            "--config",
            str(self.config),
            "--fixtures",
            str(self.fixtures),
            "--now",
            now,
            *extra,
        )

    def finalize(self, run_id):
        """准备与发送分离：手动流程需要显式结束本次准备后才允许发送。"""
        return self.ok("finalize", "--config", str(self.config), "--run-id", run_id)

    def prepare_packet_send(self):
        prepared = self.prepare()
        packet = self.packet(prepared["run_id"])
        results = support.model_results(packet)
        proc = self.decide(prepared["run_id"], packet, results)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 本场景只让首批入选；其余候选明确排除后才能完成准备。
        while True:
            remaining = self.packet(prepared["run_id"])
            if remaining.get("packet_id") is None:
                break
            skipped = self.decide(prepared["run_id"], remaining, support.model_results(remaining, action="skip"))
            self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.finalize(prepared["run_id"])
        return prepared["run_id"], packet

    def assertBrowserUntouched(self):
        self.assertFalse(
            self.marker.exists(),
            f"模拟路径不应调用 ego-browser：{self.marker}",
        )


class ConfigCheckTest(CliCase):
    def test_missing_live_flag_is_rejected(self):
        config = support.BASE_CONFIG.copy()
        config.pop("live")
        path = support.write_json(self.tmp / "broken.json", config)
        error = self.fails(2, "config", "check", "--config", str(path))
        self.assertEqual(error["code"], "invalid_config")
        self.assertIn("live", error["message"])

    def test_semantic_requirement_requires_unknown_policy(self):
        config = json.loads(json.dumps(support.BASE_CONFIG, ensure_ascii=False))
        config["hard_requirements"][2].pop("unknown_policy")
        path = support.write_json(self.tmp / "broken.json", config)
        error = self.fails(2, "config", "check", "--config", str(path))
        self.assertEqual(error["code"], "invalid_config")
        self.assertIn("unknown_policy", error["message"])

    def test_valid_config_reports_binding(self):
        payload = self.ok("config", "check", "--config", str(self.config))
        self.assertEqual(payload["account_id"], "acct-demo-0001")
        self.assertFalse(payload["live"])
        self.assertEqual(len(payload["config_digest"]), 64)


class PrepareTest(CliCase):
    def test_program_requirements_and_dedupe(self):
        prepared = self.prepare()
        rejected = {item["job_id"]: item["code"] for item in prepared["rejected"]}
        self.assertEqual(prepared["accepted"], 4)
        self.assertEqual(rejected["job-intern"], "program_requirement")
        self.assertEqual(rejected["job-remote"], "program_requirement")
        self.assertEqual(rejected["job-known"], "platform_history")
        self.assertEqual(rejected["job-dupco"], "duplicate_in_batch")
        self.assertEqual(rejected["job-dupbo"], "duplicate_in_batch")
        self.assertNotIn("job-a1", rejected, "同岗重复发现归并来源，不把同一行同时列为排除")
        table = self.cli("today", "--config", str(self.config), "--day", "2026-03-02").stdout
        self.assertEqual(table.count("/job_detail/job-a1.html"), 1)
        self.assertBrowserUntouched()

    def test_packet_batches_until_empty(self):
        run_id = self.prepare()["run_id"]
        first = self.packet(run_id)
        self.assertEqual([item["job_id"] for item in first["jobs"]], ["job-a1", "job-a2", "job-a3"])
        self.assertEqual(first["remaining"], 1)
        # 未决策前重复导出返回同一批次，便于宿主失败后继续原任务。
        self.assertEqual(self.packet(run_id)["packet_id"], first["packet_id"])
        proc = self.decide(run_id, first, support.model_results(first, action="skip"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        second = self.packet(run_id)
        self.assertEqual([item["job_id"] for item in second["jobs"]], ["job-a4"])
        self.assertEqual(second["remaining"], 0)
        self.assertEqual(self.decide(run_id, second, support.model_results(second, action="skip")).returncode, 0)
        third = self.packet(run_id)
        self.assertIsNone(third["packet_id"])
        self.assertEqual(third["jobs"], [])
        self.assertEqual(third["remaining"], 0)

    def test_incomplete_platform_history_stops_prepare(self):
        contacts = dict(support.CONTACTS, history_complete=False)
        support.write_fixtures(self.tmp / "fixtures", contacts=contacts)
        error = self.fails(
            3,
            "prepare",
            "--config",
            str(self.config),
            "--fixtures",
            str(self.fixtures),
            "--now",
            NOW_DAY1,
        )
        self.assertEqual(error["code"], "history_incomplete")


class DecideTest(CliCase):
    def _prepare(self):
        prepared = self.prepare()
        packet = self.packet(prepared["run_id"])
        return prepared["run_id"], packet

    def test_unmet_hard_requirement_blocks_send_atomically(self):
        run_id, packet = self._prepare()
        results = support.model_results(packet, statuses={("job-a2", "hr_night_shift"): "unmet"})
        error = json.loads(self.decide(run_id, packet, results).stderr)["error"]
        self.assertEqual(error["code"], "hard_requirement_unmet")
        # 整批未落盘：换成合法结果仍能一次接收全部岗位。
        accepted = self.decide(run_id, packet, support.model_results(packet))
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["ready"], 3)

    def test_unknown_follows_configured_policy(self):
        run_id, packet = self._prepare()
        blocked = support.model_results(packet, statuses={("job-a1", "hr_office"): "unknown"})
        error = json.loads(self.decide(run_id, packet, blocked).stderr)["error"]
        self.assertEqual(error["code"], "hard_requirement_unknown")
        allowed = support.model_results(packet, statuses={("job-a1", "hr_night_shift"): "unknown"})
        proc = self.decide(run_id, packet, allowed)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_incomplete_batch_is_rejected(self):
        run_id, packet = self._prepare()
        results = support.model_results(packet)
        results["results"] = results["results"][:2]
        error = json.loads(self.decide(run_id, packet, results).stderr)["error"]
        self.assertEqual(error["code"], "incomplete_batch")

    def test_resume_fact_reference_must_exist(self):
        run_id, packet = self._prepare()
        results = support.model_results(packet)
        results["results"][0]["resume_fact_ids"] = ["rf_unknown"]
        error = json.loads(self.decide(run_id, packet, results).stderr)["error"]
        self.assertEqual(error["code"], "unknown_resume_fact")

    def test_duplicate_result_is_idempotent_and_conflict_rejected(self):
        run_id, packet = self._prepare()
        results = support.model_results(packet)
        first = self.decide(run_id, packet, results)
        self.assertEqual(first.returncode, 0, first.stderr)
        again = self.decide(run_id, packet, results)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(json.loads(again.stdout)["duplicate"])
        changed = support.model_results(packet, greetings={"job-a1": "您好，换一段招呼正文。"})
        error = json.loads(self.decide(run_id, packet, changed).stderr)["error"]
        self.assertEqual(error["code"], "decision_conflict")

    def test_changed_config_invalidates_packet(self):
        run_id, packet = self._prepare()
        support.write_config(self.config, daily_limit=4)
        error = json.loads(self.decide(run_id, packet, support.model_results(packet)).stderr)["error"]
        self.assertEqual(error["code"], "config_changed")


class SendTest(CliCase):
    config_overrides: ClassVar[dict] = {"live": True}
    browser_overrides = None

    def test_simulated_send_then_duplicate_and_prior_run_dedupe(self):
        run_id, _packet = self.prepare_packet_send()
        proc = self.send(run_id)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["sent"], 3)
        self.assertBrowserUntouched()
        again_proc = self.send(run_id)
        self.assertEqual(again_proc.returncode, 3, again_proc.stderr)
        self.assertEqual(json.loads(again_proc.stderr)["error"]["code"], "nothing_ready")
        status = self.ok("status", "--config", str(self.config), "--now", NOW_DAY1)
        self.assertEqual(status["jobs"]["sent"], 3)
        self.assertEqual(len(status["attempts"]), 3)
        # 下一轮准备：已进入联系流程的岗位按去重要求排除。
        prepared = self.prepare(NOW_DAY1)
        rejected = {item["job_id"]: item["code"] for item in prepared["rejected"]}
        self.assertEqual(rejected["job-a1"], "prior_run")
        self.assertEqual(prepared["accepted"], 0)
        self.assertEqual(prepared["reused"], 1, "同日已明确排除的第四岗复用判断，不重新调用模型")

    def test_daily_limit_is_shared_across_runs(self):
        support.write_config(self.config, daily_limit=2, live=True)
        run_id, _packet = self.prepare_packet_send()
        payload = json.loads(self.send(run_id).stdout)
        self.assertEqual(payload["sent"], 2)
        self.assertEqual(self.ok("status", "--config", str(self.config), "--now", NOW_DAY1)["quota"]["used"], 2)
        prepared = self.prepare(NOW_DAY1)
        packet = self.packet(prepared["run_id"])
        self.assertIsNone(packet["packet_id"], "已满额度不能再导出岗位生成话术")
        self.finalize(prepared["run_id"])
        error = json.loads(self.send(prepared["run_id"]).stderr)["error"]
        self.assertEqual(error["code"], "nothing_ready")
        self.assertEqual(len(self.ok("status", "--config", str(self.config), "--now", NOW_DAY1)["attempts"]), 2)

    def test_stale_run_day_is_refused(self):
        run_id, _packet = self.prepare_packet_send()
        error = json.loads(self.send(run_id, now=NOW_DAY2).stderr)["error"]
        self.assertEqual(error["code"], "stale_run")

    def test_changed_config_blocks_send(self):
        run_id, _packet = self.prepare_packet_send()
        support.write_config(self.config, daily_limit=4, live=True)
        error = json.loads(self.send(run_id).stderr)["error"]
        self.assertEqual(error["code"], "config_changed")

    def test_changed_job_snapshot_blocks_send(self):
        run_id, _packet = self.prepare_packet_send()
        jobs = json.loads((self.fixtures / "jobs.json").read_text(encoding="utf-8"))
        for job in jobs:
            if job["job_id"] == "job-a1":
                job["description"] = "示例 JD 变更：新增了要求。" + job["description"]
        support.write_json(self.fixtures / "jobs.json", jobs)
        proc = self.send(run_id, "--job", "job-a1")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["refused"][0]["code"], "job_snapshot_changed")
        self.assertEqual(payload["attempted"], 0)
        detail = self.cli("today", "--config", str(self.config), "--day", "2026-03-02", "--job", "job-a1")
        self.assertIn("待重新准备", detail.stdout)

    def test_platform_history_blocks_send(self):
        run_id, _packet = self.prepare_packet_send()
        browser = support.browser_script()
        browser["contacts"] = [{"job_id": "job-a1", "company": "示例一号科技", "boss_id": "boss-01"}]
        support.write_json(self.fixtures / "browser.json", browser)
        proc = self.send(run_id, "--job", "job-a1")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["refused"][0]["code"], "platform_history")
        self.assertEqual(payload["attempted"], 0)


class SendDisabledTest(CliCase):
    def test_live_disabled_refuses_send(self):
        prepared = self.prepare()
        packet = self.packet(prepared["run_id"])
        self.assertEqual(self.decide(prepared["run_id"], packet, support.model_results(packet)).returncode, 0)
        error = json.loads(self.send(prepared["run_id"]).stderr)["error"]
        self.assertEqual(error["code"], "live_disabled")
        self.assertBrowserUntouched()


class InterruptedSendTest(CliCase):
    config_overrides: ClassVar[dict] = {"live": True}
    browser_overrides: ClassVar[dict] = {
        "job-a1": {"fail_at": "click_send"},
        "job-a2": {"reply": True},
    }

    def test_failure_and_reply_keep_uncertain_without_resend(self):
        run_id, _packet = self.prepare_packet_send()
        proc = self.send(run_id)
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["sent"], 0)
        self.assertEqual(payload["stopped"], "uncertain")
        states = {item["job_id"]: item["state"] for item in payload["attempts"]}
        self.assertEqual(states, {"job-a1": "uncertain"})
        self.assertBrowserUntouched()
        status = self.ok("status", "--config", str(self.config), "--now", NOW_DAY1)
        self.assertEqual(status["jobs"]["uncertain"], 1)
        self.assertEqual(status["jobs"]["ready"], 2)
        self.assertEqual(len(status["attempts"]), 1)
        again_proc = self.send(run_id)
        self.assertEqual(again_proc.returncode, 4, again_proc.stdout + again_proc.stderr)
        again = json.loads(again_proc.stdout)
        self.assertEqual(again["stopped"], "uncertain")
        self.assertEqual(again["sent"], 0)
        states = {item["job_id"]: item["state"] for item in again["attempts"]}
        self.assertEqual(states, {"job-a2": "uncertain"})
        status = self.ok("status", "--config", str(self.config), "--now", NOW_DAY1)
        self.assertEqual(len(status["attempts"]), 2)
        self.assertEqual(
            sorted(item["job_id"] for item in status["attempts"]),
            ["job-a1", "job-a2"],
        )


class StatusTest(CliCase):
    config_overrides: ClassVar[dict] = {"live": True}

    def test_status_reports_counts_without_content(self):
        run_id, _packet = self.prepare_packet_send()
        self.assertEqual(self.send(run_id).returncode, 0)
        payload = self.ok("status", "--config", str(self.config), "--now", NOW_DAY1)
        self.assertEqual(payload["jobs"]["sent"], 3)
        self.assertEqual(payload["quota"]["used"], 3)
        self.assertEqual(payload["quota"]["limit"], 5)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("示例事实", text)
        self.assertNotIn("负责订单服务开发", text)


if __name__ == "__main__":
    unittest.main()
