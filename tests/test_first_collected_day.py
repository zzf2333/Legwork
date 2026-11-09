"""首次采集日期：平台拿不到发布时间时的新岗位代理信号。

覆盖取批排序、批次字段、快照摘要不变和旧数据目录兼容。全部岗位、公司、招聘者均为虚构示例。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import support

from legwork import config as config_module
from legwork import records, workflow
from legwork.store import Store

NOW_DAY1 = "2026-03-02T10:00:00+08:00"
NOW_DAY2 = "2026-03-03T10:00:00+08:00"


def job_row(job_id, *, boss_active="", company_size="", first_collected_day=None):
    row = {
        "job_id": job_id,
        "company": "示例公司" + job_id,
        "boss_id": "boss-" + job_id,
        "boss_name": "示例招聘者",
        "title": "Python 后端工程师",
        "salary": "18-25K",
        "address": "示例城市·高新区",
        "description": "示例 JD：负责订单服务开发，常驻示例城市办公室。",
        "boss_active": boss_active,
        "company_size": company_size,
    }
    if first_collected_day is not None:
        row["first_collected_day"] = first_collected_day
    return row


class PreferenceKeyTest(unittest.TestCase):
    def test_newer_first_collected_day_sorts_first(self):
        """新采集到的岗位排在前面，优先于同一天里规模和活跃的偏好。"""
        fresh = job_row("job-z", boss_active="半年前活跃", company_size="10000人以上",
                        first_collected_day="2026-03-03")
        small_recent = job_row("job-a", boss_active="刚刚活跃", company_size="20-99人",
                               first_collected_day="2026-03-02")
        self.assertLess(records.preference_key(fresh), records.preference_key(small_recent))

    def test_same_day_keeps_size_then_activity_then_id_order(self):
        same_day = "2026-03-02"
        small = job_row("job-b", boss_active="半年前活跃", company_size="20-99人",
                        first_collected_day=same_day)
        medium = job_row("job-a", boss_active="刚刚活跃", company_size="1000-9999人",
                         first_collected_day=same_day)
        recent = job_row("job-c", boss_active="刚刚活跃", company_size="20-99人",
                         first_collected_day=same_day)
        earlier_id = job_row("job-a", boss_active="刚刚活跃", company_size="20-99人",
                             first_collected_day=same_day)
        self.assertLess(records.preference_key(recent), records.preference_key(small))
        self.assertLess(records.preference_key(small), records.preference_key(medium))
        self.assertLess(records.preference_key(earlier_id), records.preference_key(recent))

    def test_missing_first_collected_day_sorts_last_without_error(self):
        known = job_row("job-z", company_size="10000人以上", first_collected_day="2026-03-01")
        unknown = job_row("job-a", company_size="20-99人")
        self.assertLess(records.preference_key(known), records.preference_key(unknown))
        # 全都没有记录时保持原有顺序，不报错。
        other = job_row("job-b", company_size="1000-9999人")
        self.assertLess(records.preference_key(unknown), records.preference_key(other))

    def test_first_collected_day_stays_out_of_digests(self):
        """首次采集日期是本工具的本地事实：不进快照摘要，不影响同日复用与发送前比对。"""
        base = job_row("job-a1")
        changed = base | {"first_collected_day": "2026-03-03"}
        self.assertEqual(records.snapshot_digest(base), records.snapshot_digest(changed))
        self.assertEqual(records.send_digest(base), records.send_digest(changed))


class FirstCollectedDaysStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.account = support.BASE_CONFIG["account_id"]
        self.store = self.enterContext(Store(self.tmp / "state.sqlite3"))
        self.store.bind_account(self.account)
        self.store.require_mode("fixtures")

    def sources(self, job_id, *days, account=None):
        for day in days:
            self.store.record_source(
                account or self.account, day, job_id, "run-" + day, "Python 后端", day + "T10:00:00"
            )

    def test_returns_earliest_day_per_job(self):
        self.sources("job-a1", "2026-03-01", "2026-03-02", "2026-03-03")
        self.sources("job-a2", "2026-03-02")
        self.assertEqual(
            self.store.first_collected_days(self.account),
            {"job-a1": "2026-03-01", "job-a2": "2026-03-02"},
        )

    def test_ignores_other_accounts_and_jobs_without_sources(self):
        self.sources("job-a1", "2026-03-01")
        self.sources("job-a2", "2026-03-01", account="acct-other-0002")
        self.assertEqual(self.store.first_collected_days(self.account), {"job-a1": "2026-03-01"})


class PacketFirstCollectedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.tmp / "data"
        self.config = config_module.load(
            support.write_config(self.tmp / "config.json", batch_size=6, daily_limit=10)
        )

    def build(self, jobs, sources):
        now = datetime.fromisoformat(NOW_DAY2)
        with Store(self.data / "state.sqlite3") as store:
            store.bind_account(self.config.account_id)
            store.require_mode("fixtures")
            store.create_run(
                "run-order", self.config.account_id, "fixtures", now.date().isoformat(),
                self.config.digest, NOW_DAY2,
            )
            for job in jobs:
                store.add_job(
                    "run-order", self.config.account_id, job, records.snapshot_digest(job),
                    "awaiting_decisions", job_url=records.job_url(job["job_id"]),
                )
            for job_id, day in sources:
                store.record_source(
                    self.config.account_id, day, job_id, "run-" + day, "Python 后端", day + "T10:00:00"
                )
            return workflow.build_packet(
                store, self.config, store.run("run-order"), self.data, now
            )

    def test_new_job_comes_first_and_packet_reports_first_day(self):
        summary = self.build(
            [
                job_row("job-old", boss_active="今日活跃", company_size="20-99人"),
                job_row("job-mid", boss_active="今日活跃", company_size="1000-9999人"),
                job_row("job-new", boss_active="半年前活跃", company_size="10000人以上"),
            ],
            [
                ("job-old", "2026-03-01"),
                ("job-mid", "2026-03-01"),
                ("job-new", "2026-03-03"),
            ],
        )
        self.assertEqual(summary["jobs"], ["job-new", "job-old", "job-mid"])
        document = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            [item["first_collected_day"] for item in document["jobs"]],
            ["2026-03-03", "2026-03-01", "2026-03-01"],
        )

    def test_job_without_source_row_keeps_existing_order(self):
        """旧数据目录里没有采集记录的岗位：照旧取批，字段为空字符串，不报错。"""
        summary = self.build(
            [
                job_row("job-large", boss_active="刚刚活跃", company_size="10000人以上"),
                job_row("job-small", boss_active="刚刚活跃", company_size="20-99人"),
            ],
            [],
        )
        self.assertEqual(summary["jobs"], ["job-small", "job-large"])
        document = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
        self.assertEqual([item["first_collected_day"] for item in document["jobs"]], ["", ""])


class TwoDayRunTest(unittest.TestCase):
    """真实 CLI 入口：第二天新出现的岗位排在前面，批次带首次采集日期，decide 仍能接收。"""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.tmp / "data"
        self.config = support.write_config(self.tmp / "config.json", batch_size=4, daily_limit=10)
        self.fixtures = support.write_fixtures(self.tmp / "fixtures", jobs=support.JOBS[:2])
        self.new_job = support.job(
            "job-new1", "示例八号科技", "boss-08", "Python 平台工程师", "示例城市·金牛区",
            "新发布的岗位，负责平台工具开发，常驻示例城市办公室。",
        )

    def cli(self, *args):
        return support.run_cli(self.tmp, self.data, *args)

    def prepare(self, now):
        proc = self.cli(
            "prepare", "--config", str(self.config), "--fixtures", str(self.fixtures), "--now", now
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_new_job_is_first_the_next_day(self):
        self.prepare(NOW_DAY1)
        support.write_fixtures(self.tmp / "fixtures", jobs=support.JOBS[:2] + [self.new_job])
        run = self.prepare(NOW_DAY2)
        packet = self.cli("packet", "--config", str(self.config), "--run-id", run["run_id"])
        self.assertEqual(packet.returncode, 0, packet.stderr)
        summary = json.loads(packet.stdout)
        document = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            [item["job_id"] for item in document["jobs"]],
            ["job-new1", "job-a1", "job-a2"],
        )
        self.assertEqual(
            [item["first_collected_day"] for item in document["jobs"]],
            ["2026-03-03", "2026-03-02", "2026-03-02"],
        )
        results = support.write_json(self.tmp / "results.json", support.model_results(document))
        decided = self.cli(
            "decide", "--config", str(self.config), "--run-id", run["run_id"],
            "--packet", summary["path"], "--results", str(results),
        )
        self.assertEqual(decided.returncode, 0, decided.stderr)


if __name__ == "__main__":
    unittest.main()
