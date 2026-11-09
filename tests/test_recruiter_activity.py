"""招聘方活跃时间与公司规模：分档、配置声明、准备期排除、取批顺序、侧表存储。

全部岗位、公司与招聘者均为虚构示例。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import support

from legwork import config as config_module
from legwork import records, workflow
from legwork.store import Store

NOW = "2026-03-02T10:00:00+08:00"
ACTIVITY_RULE = {"id": "hr_active", "kind": "activity", "text": "招聘方最近一周内活跃"}


def job_row(job_id, *, boss_active="", company_size=""):
    return {
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


class ActivityBucketTest(unittest.TestCase):
    def test_known_activity_texts(self):
        cases = {
            "在线": "一周内",
            "刚刚活跃": "一周内",
            "今日活跃": "一周内",
            "3日内活跃": "一周内",
            "本周活跃": "一周内",
            "2周内活跃": "一周外",
            "本月活跃": "一周外",
            "3月内活跃": "一周外",
            "半年前活跃": "一周外",
            "近半年活跃": "一周外",
            "": "",
            "   ": "",
            "昨天活跃": "",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(records.activity_bucket(text), expected)


class CompanySizeBucketTest(unittest.TestCase):
    def test_known_size_texts(self):
        cases = {
            "0-20人": "中小",
            "20-99人": "中小",
            "100-499人": "中小",
            "500-999人": "中小",
            "1000-9999人": "中大型",
            "10000人以上": "大型",
            "1万人以上": "大型",
            "": "",
            "规模未知": "",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(records.company_size_bucket(text), expected)


class PreferenceKeyTest(unittest.TestCase):
    def test_small_and_recent_sorts_before_large(self):
        small_recent = job_row("job-b", boss_active="刚刚活跃", company_size="20-99人")
        small_stale = job_row("job-a", boss_active="半年前活跃", company_size="20-99人")
        large_recent = job_row("job-0", boss_active="刚刚活跃", company_size="10000人以上")
        self.assertLess(records.preference_key(small_recent), records.preference_key(small_stale))
        self.assertLess(records.preference_key(small_stale), records.preference_key(large_recent))

    def test_unknown_size_and_activity_sort_last(self):
        unknown = job_row("job-1")
        large = job_row("job-2", boss_active="刚刚活跃", company_size="10000人以上")
        self.assertGreater(records.preference_key(unknown), records.preference_key(large))

    def test_daily_changing_values_stay_out_of_digests(self):
        """活跃时间与规模每天变化：不进快照摘要，不影响同日复用与发送前 JD 比对。"""
        base = job_row("job-a1")
        changed = base | {"boss_active": "刚刚活跃", "company_size": "10000人以上"}
        self.assertEqual(records.snapshot_digest(base), records.snapshot_digest(changed))
        self.assertEqual(records.send_digest(base), records.send_digest(changed))


class ActivityConfigTest(unittest.TestCase):
    @staticmethod
    def document(**fields):
        document = json.loads(json.dumps(support.BASE_CONFIG, ensure_ascii=False))
        document["hard_requirements"] = list(document["hard_requirements"]) + [
            dict(ACTIVITY_RULE, **fields)
        ]
        return document

    def test_activity_requirement_stays_out_of_program_and_model_input(self):
        config = config_module.parse(self.document())
        requirement = next(item for item in config.requirements if item.kind == "activity")
        self.assertTrue(config.activity_required)
        self.assertEqual((requirement.id, requirement.text), ("hr_active", "招聘方最近一周内活跃"))
        self.assertEqual(config.program_requirements, tuple(
            item for item in config.requirements if item.kind == "program"
        ))
        self.assertNotIn("hr_active", [item.id for item in config.program_requirements])
        model_ids = [item["id"] for item in config.model_summary()["hard_requirements"]]
        self.assertNotIn("hr_active", model_ids)

    def test_activity_requirement_refuses_text_matching_fields(self):
        for fields in (
            {"field": "title"},
            {"op": "contains"},
            {"value": "最近一周"},
            {"unknown_policy": "skip"},
            {"priority": 1},
        ):
            with self.subTest(fields=fields), self.assertRaises(config_module.ConfigError):
                config_module.parse(self.document(**fields))

    def test_config_without_activity_rule_does_not_screen_by_activity(self):
        config = config_module.parse(json.loads(json.dumps(support.BASE_CONFIG)))
        self.assertFalse(config.activity_required)


class PrepareActivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.tmp / "data"
        self.fixtures = support.write_fixtures(self.tmp / "fixtures", jobs=self.jobs())

    @staticmethod
    def jobs():
        observed = {
            "job-a1": ("刚刚活跃", "100-499人"),
            "job-a2": ("半年前活跃", "1000-9999人"),
            "job-a3": ("", "0-20人"),
        }
        rows = []
        for item in support.JOBS:
            active, size = observed.get(item["job_id"], ("", ""))
            rows.append(dict(item, boss_active=active, company_size=size))
        return rows

    def config(self, requirements):
        return support.write_config(self.tmp / "config.json", hard_requirements=requirements)

    def prepare(self, config):
        proc = support.run_cli(
            self.tmp, self.data, "prepare", "--config", str(config), "--fixtures",
            str(self.fixtures), "--now", NOW,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def requirements(self, *, activity: bool):
        items = [dict(item) for item in support.BASE_CONFIG["hard_requirements"]]
        return items + ([ACTIVITY_RULE] if activity else [])

    def test_stale_recruiter_is_excluded_before_the_model(self):
        summary = self.prepare(self.config(self.requirements(activity=True)))
        rejected = {item["job_id"]: (item["code"], item["detail"]) for item in summary["rejected"]}
        self.assertEqual(rejected["job-a2"], ("inactive_recruiter", "招聘方活跃：半年前活跃"))
        with Store(self.data / "state.sqlite3") as store:
            stale = store.job(summary["run_id"], "job-a2")
            assert stale is not None
            self.assertEqual((stale["status"], stale["status_reason"]),
                             ("excluded", "inactive_recruiter"))
            kept = store.job(summary["run_id"], "job-a1")
            assert kept is not None
            self.assertEqual((kept["status"], kept["boss_active"]), ("awaiting_decisions", "刚刚活跃"))
            # 活跃信息读不到不等于久未活跃：仍然待判断，公司规模照常保存。
            missed = store.job(summary["run_id"], "job-a3")
            assert missed is not None
            self.assertEqual((missed["status"], missed["company_size"]),
                             ("awaiting_decisions", "0-20人"))

    def test_today_labels_the_exclusion_in_chinese(self):
        config = self.config(self.requirements(activity=True))
        self.prepare(config)
        proc = support.run_cli(self.tmp, self.data, "today", "--config", str(config), "--now", NOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("招聘方活跃时间超过一周", proc.stdout)

    def test_without_activity_rule_stale_recruiter_stays_candidate(self):
        summary = self.prepare(self.config(self.requirements(activity=False)))
        codes = {item["job_id"]: item["code"] for item in summary["rejected"]}
        self.assertNotIn("inactive_recruiter", codes.values())
        with Store(self.data / "state.sqlite3") as store:
            stored = store.job(summary["run_id"], "job-a2")
            assert stored is not None
            self.assertEqual(stored["status"], "awaiting_decisions")


class PacketOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data = self.tmp / "data"
        self.config = config_module.load(
            support.write_config(self.tmp / "config.json", batch_size=6, daily_limit=10)
        )

    def build(self, jobs):
        now = datetime.fromisoformat(NOW)
        with Store(self.data / "state.sqlite3") as store:
            store.bind_account(self.config.account_id)
            store.require_mode("fixtures")
            store.create_run(
                "run-order", self.config.account_id, "fixtures", now.date().isoformat(),
                self.config.digest, NOW,
            )
            for job in jobs:
                store.add_job(
                    "run-order", self.config.account_id, job, records.snapshot_digest(job),
                    "awaiting_decisions", job_url=records.job_url(job["job_id"]),
                )
            return workflow.build_packet(
                store, self.config, store.run("run-order"), self.data, now
            )

    def test_small_recent_first_and_large_last(self):
        summary = self.build([
            job_row("job-large", boss_active="刚刚活跃", company_size="10000人以上"),
            job_row("job-mid", boss_active="刚刚活跃", company_size="1000-9999人"),
            job_row("job-stale", boss_active="半年前活跃", company_size="100-499人"),
            job_row("job-idle", boss_active="", company_size="20-99人"),
            job_row("job-hot", boss_active="今日活跃", company_size="20-99人"),
        ])
        self.assertEqual(
            summary["jobs"],
            ["job-hot", "job-idle", "job-stale", "job-mid", "job-large"],
        )
        document = json.loads(Path(summary["path"]).read_text(encoding="utf-8"))
        first = document["jobs"][0]
        self.assertEqual((first["boss_active"], first["company_size"]), ("今日活跃", "20-99人"))
        self.assertEqual(
            set(document["hard_requirements"][0]), {"id", "text", "unknown_policy"}
        )


class JobMetaStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.tmp / "state.sqlite3"
        self.account = support.BASE_CONFIG["account_id"]

    def open_store(self):
        store = Store(self.path)
        store.bind_account(self.account)
        return store

    def create_run(self, store, run_id="run-1"):
        store.require_mode("fixtures")
        store.create_run(run_id, self.account, "fixtures", "2026-03-02", "cfg", NOW)

    def test_activity_and_size_round_trip(self):
        with self.open_store() as store:
            self.create_run(store)
            store.add_job(
                "run-1", self.account,
                job_row("job-a1", boss_active="刚刚活跃", company_size="500-999人"),
                "digest", "awaiting_decisions",
            )
            store.add_job("run-1", self.account, job_row("job-a2"), "digest", "excluded")
            stored = store.job("run-1", "job-a1")
            assert stored is not None
            self.assertEqual((stored["boss_active"], stored["company_size"]),
                             ("刚刚活跃", "500-999人"))
            rows = {item["job_id"]: item for item in store.jobs("run-1")}
            self.assertEqual((rows["job-a2"]["boss_active"], rows["job-a2"]["company_size"]),
                             ("", ""))
            self.assertEqual(store.jobs("run-1", "excluded")[0]["boss_active"], "")

    def test_data_dir_without_job_meta_table_still_opens(self):
        """旧目录（建库时还没有 job_meta）能打开：打开时补建侧表，缺行读作空字符串。"""
        with self.open_store() as store:
            self.create_run(store)
            store.add_job(
                "run-1", self.account,
                job_row("job-a1", boss_active="本月活跃", company_size="1000-9999人"),
                "digest", "awaiting_decisions",
            )
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TABLE job_meta")
        with self.open_store() as store:
            self.assertIsNotNone(store.run("run-1"))
            stored = store.job("run-1", "job-a1")
            assert stored is not None
            self.assertEqual((stored["boss_active"], stored["company_size"]), ("", ""))
            store.add_job(
                "run-1", self.account, job_row("job-a2", boss_active="在线"), "digest",
                "awaiting_decisions",
            )
            written = store.job("run-1", "job-a2")
            assert written is not None
            self.assertEqual(written["boss_active"], "在线")


if __name__ == "__main__":
    unittest.main()
