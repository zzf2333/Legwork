"""补发契约：只对结果不明的岗位，向已存在会话发一条定制正文。

全部走真实 CLI 与隔离数据目录；岗位、公司、联系人都是虚构脱敏数据。
"""
import json
import sqlite3
import unittest

import support
from test_daily import NOW_DAY1, DailyCase, custom


class FollowUpTest(DailyCase):
    def uncertain(self, job_id="job-full1"):
        """先造出一次点击后失败（uncertain）的发送，再清掉故障注入。"""
        prepared, fixtures = self.prepare([custom(job_id)])
        _, document = self.packet(prepared["run_id"])
        self.decide(prepared["run_id"], document)
        path = fixtures / "browser.json"
        script = json.loads(path.read_text(encoding="utf-8"))
        script["jobs"][job_id]["fail_at"] = "click_send"
        support.write_json(path, script)
        proc = self.send(prepared["run_id"], fixtures=fixtures)
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["uncertain"], 1)
        script["jobs"][job_id]["fail_at"] = None
        support.write_json(path, script)
        # 平台侧已产生会话：补齐 contacts 反映点击后的真实状态
        script["contacts"].append(
            {"job_id": job_id, "company": "示例一号科技", "boss_id": "boss-01"}
        )
        support.write_json(path, script)
        return prepared, fixtures

    def followup(self, run_id, fixtures, *jobs):
        args = [
            "followup", "--config", str(self.config_path), "--run-id", run_id,
            "--fixtures", str(fixtures), "--now", NOW_DAY1,
        ]
        for job_id in jobs:
            args += ["--job", job_id]
        return self.cli(*args)

    def test_sends_custom_greeting_into_existing_conversation(self):
        prepared, fixtures = self.uncertain()
        proc = self.followup(prepared["run_id"], fixtures, "job-full1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["sent"], 1)
        self.assertEqual(payload["uncertain"], 0)
        self.assertFalse(payload["quota_consumed"], "补发不新增首次沟通占用")

        status = json.loads(self.ok("status", "--config", str(self.config_path)))
        self.assertEqual(status["jobs"]["sent"], 1)
        self.assertEqual(status["jobs"]["uncertain"], 0)
        with sqlite3.connect(self.data / "state.sqlite3") as db:
            rows = [row[0] for row in db.execute("SELECT state FROM attempts")]
        self.assertEqual(rows, ["sent"], "补发复用同一条占用，不新增首次沟通记录")
        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn("状态：已发送", detail)
        self.assertFalse(self.marker.exists())

    def test_refuses_job_without_uncertain_attempt(self):
        prepared, fixtures = self.prepare([custom("job-full1")])
        _, document = self.packet(prepared["run_id"])
        self.decide(prepared["run_id"], document)
        sent = self.send(prepared["run_id"], fixtures=fixtures)
        self.assertEqual(sent.returncode, 0, sent.stdout + sent.stderr)

        proc = self.followup(prepared["run_id"], fixtures, "job-full1")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "nothing_uncertain")
        self.assertIn("状态：已发送", self.today("--day", "2026-03-02", "--job", "job-full1"))

    def test_requires_explicit_job(self):
        prepared, fixtures = self.uncertain()
        proc = self.cli(
            "followup", "--config", str(self.config_path), "--run-id", prepared["run_id"],
            "--fixtures", str(fixtures), "--now", NOW_DAY1,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("--job", proc.stderr)

    def test_missing_conversation_is_refused_before_sending(self):
        prepared, fixtures = self.uncertain()
        path = fixtures / "browser.json"
        script = json.loads(path.read_text(encoding="utf-8"))
        script["contacts"] = []
        support.write_json(path, script)
        proc = self.followup(prepared["run_id"], fixtures, "job-full1")
        self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["sent"], 0)
        self.assertEqual(payload["attempts"][0]["error"], "contact_missing")
        self.assertIn("状态：结果待核对（uncertain）", self.today("--day", "2026-03-02", "--job", "job-full1"))


if __name__ == "__main__":
    unittest.main()
