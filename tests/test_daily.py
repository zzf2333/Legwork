"""今日清单契约：一岗一行、完整留存、同日来源、复用与失效、跨日历史与 CLI 查看。

全部走真实 CLI 与隔离数据目录；岗位、公司、联系人都是虚构脱敏数据。
"""
import json
import tempfile
import unittest
from pathlib import Path

import support

from legwork import sources
from legwork.errors import PolicyError
from legwork.records import job_url, snapshot_digest
from legwork.store import Store, StoreError

NOW_DAY1 = "2026-03-02T10:00:00+08:00"
NOW_DAY2 = "2026-03-03T10:00:00+08:00"
LONG_JD = (
    "负责订单服务开发，要求五年以上 Python 经验。\n\n"
    + "岗位职责：\n" * 120
    + "完整JD结尾标记"
)


class DailyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="boss-daily-"))
        self.addCleanup(_cleanup, self.tmp)
        self.data = self.tmp / "data"
        self.config_path = support.write_config(
            self.tmp / "config.json", live=True, batch_size=3
        )
        self.marker = support.decoy_browser(self.tmp / "bin")

    # -- 辅助 ---------------------------------------------------------------
    def cli(self, *args):
        return support.run_cli(self.tmp, self.data, *args, path_prefix=self.tmp / "bin")

    def ok(self, *args):
        proc = self.cli(*args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def browser_script(self, jobs):
        return {
            "platform_user_id": support.BASE_CONFIG["platform_user_id"],
            "history_complete": True,
            "contacts": [],
            "jobs": {
                item["job_id"]: {
                    "default_message": support.DEFAULT_GREETING,
                    "reply": False,
                    "fail_at": None,
                }
                for item in jobs
                if item["job_id"]
            },
        }

    def fixtures(self, jobs, name="fixtures"):
        return support.write_fixtures(
            self.tmp / name, jobs=jobs, browser=self.browser_script(jobs)
        )

    def prepare(self, jobs, now=NOW_DAY1, name="fixtures"):
        fixtures = self.fixtures(jobs, name)
        payload = json.loads(
            self.ok(
                "prepare", "--config", str(self.config_path), "--fixtures", str(fixtures),
                "--now", now,
            )
        )
        return payload, fixtures

    def packet(self, run_id):
        summary = json.loads(
            self.ok("packet", "--config", str(self.config_path), "--run-id", run_id)
        )
        if summary["path"] is None:
            return summary, None
        return summary, json.loads(Path(summary["path"]).read_text(encoding="utf-8"))

    def decide(self, run_id, document, *, action="send"):
        results = support.write_json(
            self.tmp / "results.json", support.model_results(document, action=action)
        )
        summary, _ = self.packet(run_id)
        return self.ok(
            "decide", "--config", str(self.config_path), "--run-id", run_id,
            "--packet", summary["path"], "--results", str(results),
        )

    def today(self, *extra):
        return self.ok("today", "--config", str(self.config_path), *extra)

    def send(self, run_id, now=NOW_DAY1, fixtures=None):
        return support.run_cli(
            self.tmp, self.data, "send", "--config", str(self.config_path), "--run-id", run_id,
            "--fixtures", str(fixtures or self.tmp / "fixtures"), "--now", now,
            path_prefix=self.tmp / "bin",
        )


def _cleanup(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def custom(job_id, **overrides):
    base = support.job(
        job_id, "示例一号科技", "boss-01", "Python 后端工程师", "示例城市·高新区", "常规岗位描述。"
    )
    base.update(overrides)
    return base


class SnapshotContractTest(unittest.TestCase):
    def test_job_url_is_canonical_and_rejects_external_input(self):
        self.assertEqual(
            job_url("abc123XYZ"), "https://www.zhipin.com/job_detail/abc123XYZ.html"
        )
        for bad in ("", "../x", "https://evil.example/job?id=1", "a/b", "job id", "job?id=1#x"):
            with self.assertRaises(PolicyError):
                job_url(bad)

    def test_snapshot_digest_only_normalizes_whitespace(self):
        base = custom("job-norm1", description="负责服务开发。\n\n  要求五年经验。")
        spaced = custom("job-norm1", description="负责服务开发。 要求五年经验。")
        changed = custom("job-norm1", description="负责服务开发。要求六年经验。")
        self.assertEqual(snapshot_digest(base), snapshot_digest(spaced))
        self.assertNotEqual(snapshot_digest(base), snapshot_digest(changed))
        # 链接与来源关键词是定位信息，不参与正文摘要。
        same = custom("job-norm1", description="负责服务开发。 要求五年经验。")
        same["source_keyword"] = "完全不同的关键词"
        self.assertEqual(snapshot_digest(base), snapshot_digest(same))


class SchemaGateTest(unittest.TestCase):
    def test_old_schema_directory_is_refused_without_migration(self):
        import sqlite3

        with tempfile.TemporaryDirectory(prefix="boss-schema-") as tmp:
            path = Path(tmp) / "state.sqlite3"
            db = sqlite3.connect(path)
            db.executescript(
                "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);"
                "CREATE TABLE legacy (x TEXT);"
                "INSERT INTO meta VALUES ('schema_version', '3');"
            )
            db.commit()
            db.close()
            with self.assertRaises(StoreError):
                Store(path)
            db = sqlite3.connect(path)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = db.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()[0]
            db.close()
            self.assertEqual(tables, {"meta", "legacy"}, "旧库不能在写表或索引前被改动")
            self.assertEqual(version, "3")


class FakeLiveBrowser:
    """采集边界替身：只返回平台收集形状，不启动浏览器。"""

    mode = "live"

    def __init__(self, can_add_friend=True):
        self.opened = []
        self.can_add_friend = can_add_friend

    def load_history(self):
        return {"history_complete": True, "contacts": []}

    def search(self, keyword, city_code):
        return {
            "rows": [
                {"job_id": "job-ok1", "company": "示例一号科技", "boss_id": "boss-01",
                 "title": "Python 后端工程师", "salary": "18-25K", "address": "示例城市",
                 "href_id": "job-ok1"},
                {"job_id": "job-bad1", "company": "示例二号科技", "boss_id": "boss-02",
                 "title": "平台工程师", "salary": "20-30K", "address": "示例城市",
                 "href_id": "job-other"},
                {"job_id": "", "company": "示例三号科技", "boss_id": "boss-03",
                 "title": "无稳定 ID 岗位", "salary": "10-12K", "address": "示例城市"},
            ]
        }

    def open_job(self, job):
        self.opened.append(job["job_id"])
        return {}

    def card_state(self):
        return {
            "job_id": "job-ok1", "company": "示例一号科技", "boss_id": "boss-01",
            "boss_name": "招聘者01", "title": "Python 后端工程师", "salary": "18-25K",
            "address": "示例城市", "description": "负责后端服务。",
            "can_add_friend": self.can_add_friend,
        }


class EgoSourceObservationTest(unittest.TestCase):
    def collect(self, can_add_friend=True):
        from datetime import datetime

        from legwork import config as config_module

        config = config_module.parse(dict(support.BASE_CONFIG) | {"keywords": ["Python 后端"]})
        browser = FakeLiveBrowser(can_add_friend)
        snapshot = sources.EgoSource(browser).collect(
            config, datetime.fromisoformat(NOW_DAY1)
        )
        return snapshot, browser

    def test_missing_identity_and_link_mismatch_become_anomalies(self):
        snapshot, browser = self.collect()
        self.assertEqual(browser.opened, ["job-ok1"], "链接身份不一致时不能打开目标")
        self.assertEqual([item["job_id"] for item in snapshot["jobs"]], ["job-ok1", "job-bad1"])
        self.assertEqual(snapshot["jobs"][1]["observation_error"], "link_identity_mismatch")
        reasons = sorted(item["reason"] for item in snapshot["anomalies"])
        self.assertEqual(reasons, ["link_identity_mismatch", "missing_identity"])

    def test_not_first_contact_row_is_kept_with_reason(self):
        snapshot, _ = self.collect(can_add_friend=False)
        self.assertEqual(len(snapshot["jobs"]), 2)
        self.assertIs(snapshot["jobs"][0]["can_add_friend"], False)
        self.assertEqual(snapshot["jobs"][1]["job_id"], "job-bad1")


class ListContentTest(DailyCase):
    def test_d01_keeps_every_observed_entry_and_full_jd(self):
        jobs = [
            custom("job-full1", description=LONG_JD),
            support.job(
                "job-intern1", "示例五号科技", "boss-05", "Python 开发实习生", "示例城市·青羊区",
                "实习岗位。",
            ),
            custom("job-miss1", address=""),
            {"job_id": "", "company": "示例无号科技", "boss_id": "boss-x", "boss_name": "招聘者",
             "title": "无稳定ID岗位", "salary": "10-12K", "address": "示例城市", "description": "描述"},
        ]
        prepared, _ = self.prepare(jobs)
        self.assertEqual(prepared["accepted"], 1)
        self.assertEqual(prepared["anomalies"], 1)
        codes = {item["job_id"]: item["code"] for item in prepared["rejected"]}
        self.assertEqual(codes["job-intern1"], "program_requirement")
        self.assertEqual(codes["job-miss1"], "missing_detail")
        self.assertEqual(codes[""], "missing_job_id")

        table = self.today("--day", "2026-03-02")
        self.assertIn("今日投递清单 2026-03-02", table)
        self.assertIn("示例一号科技", table)
        self.assertIn("采集排除", table)
        self.assertIn("缺少字段", table)
        self.assertIn("采集异常 1 条", table)
        self.assertNotIn("无稳定ID岗位", table, "无 ID 条目只进采集异常，不进清单")

        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn("完整JD结尾标记", detail, "已有完整 JD 不能被截断")
        self.assertIn("https://www.zhipin.com/job_detail/job-full1.html", detail)
        missing = self.today("--day", "2026-03-02", "--job", "job-miss1")
        self.assertIn("办公地址：未知", missing, "未取得字段标未知，不伪造")
        self.assertFalse(self.marker.exists())

    def test_d02_same_day_merges_sources_but_keeps_distinct_jobs(self):
        first, _fixtures = self.prepare(
            [
                custom("job-full1", source_keyword="词一"),
                support.job(
                    "job-other1", "示例三号科技", "boss-03", "后端开发工程师", "示例城市·武侯区",
                    "维护交易链路服务。",
                ),
            ]
        )
        second, _ = self.prepare(
            [
                custom("job-full1", source_keyword="词二"),
                custom("job-dupco1", boss_id="boss-07", title="数据平台工程师", source_keyword="词二"),
            ],
            name="fixtures2",
        )
        rejected = {item["job_id"]: item["code"] for item in second["rejected"]}
        self.assertEqual(rejected["job-full1"], "duplicate_in_batch")
        self.assertEqual(second["accepted"], 1, "同公司另一岗位单独留记录")

        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn("词一", detail)
        self.assertIn("词二", detail)
        self.assertEqual(detail.count("今日来源："), 1)
        self.assertIn(first["run_id"], detail)
        self.assertIn(second["run_id"], detail)
        table = self.today("--day", "2026-03-02")
        self.assertEqual(table.count("Python 后端工程师"), 1, "同岗同日只显示一行")
        self.assertEqual(table.count("示例一号科技"), 2, "同公司不同岗位分别保留")
        self.assertIn("示例三号科技", table)
        self.assertFalse(self.marker.exists())

    def test_d03_sent_attempt_and_uncertain_are_never_reset(self):
        prepared, fixtures = self.prepare([custom("job-full1")])
        _, document = self.packet(prepared["run_id"])
        self.decide(prepared["run_id"], document)
        sent = self.send(prepared["run_id"], fixtures=fixtures)
        self.assertEqual(sent.returncode, 0, sent.stdout + sent.stderr)
        self.assertEqual(json.loads(sent.stdout)["sent"], 1)

        again, _ = self.prepare([custom("job-full1")], name="fixtures2")
        codes = {item["job_id"]: item["code"] for item in again["rejected"]}
        self.assertEqual(codes["job-full1"], "prior_run")
        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn("状态：已发送", detail)
        self.assertIn("执行结果：sent", detail)
        # 跨日重采集仍按历史去重，旧结果只读保留。
        next_day, _ = self.prepare([custom("job-full1")], now=NOW_DAY2, name="fixtures3")
        codes = {item["job_id"]: item["code"] for item in next_day["rejected"]}
        self.assertEqual(codes["job-full1"], "prior_run")
        self.assertIn("状态：已发送", self.today("--day", "2026-03-02", "--job", "job-full1"))

    def test_d04_reuses_same_day_decision_without_second_model_call(self):
        prepared, _ = self.prepare([custom("job-full1")])
        _, document = self.packet(prepared["run_id"])
        self.decide(prepared["run_id"], document)
        again, _ = self.prepare([custom("job-full1")], name="fixtures2")
        self.assertEqual(again["reused"], 1)
        self.assertEqual(again["accepted"], 0)
        summary, _ = self.packet(again["run_id"])
        self.assertIsNone(summary["path"], "同日同配置同 JD 已有效结果，不再导出批次调用模型")
        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn("状态：待发送", detail)

    def test_d09_changed_config_or_jd_marks_ready_for_reprepare(self):
        prepared, _ = self.prepare([custom("job-full1")])
        _, document = self.packet(prepared["run_id"])
        self.decide(prepared["run_id"], document)

        support.write_config(self.tmp / "config.json", live=True, batch_size=3, daily_limit=4)
        again, _ = self.prepare([custom("job-full1")], name="fixtures2")
        self.assertEqual(again["accepted"], 1, "配置变化后旧结果不能直接复用")
        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn("配置已变化，需重新准备", detail)
        proc = self.send(prepared["run_id"])
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "config_changed")
        self.assertNotIn("状态：已发送", self.today("--day", "2026-03-02", "--job", "job-full1"))

    def test_d09_changed_jd_invalidates_saved_greeting(self):
        prepared, _ = self.prepare([custom("job-full1")])
        _, document = self.packet(prepared["run_id"])
        self.decide(prepared["run_id"], document)
        again, _ = self.prepare(
            [custom("job-full1", description="岗位要求已更新：需要六年经验。")], name="fixtures2"
        )
        self.assertEqual(again["accepted"], 1)
        self.assertEqual(again["reused"], 0)
        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn("JD 已变化，需重新准备", detail)
        self.assertIn("状态：待判断", detail)

    def test_d11_history_dedupe_is_recorded_with_reason(self):
        contacts = {
            "platform_user_id": support.BASE_CONFIG["platform_user_id"],
            "history_complete": True,
            "contacts": [{"job_id": "job-known1", "company": "示例九号公司", "boss_id": "boss-09"}],
        }
        fixtures = support.write_fixtures(
            self.tmp / "fixtures",
            jobs=[support.job("job-known1", "示例九号公司", "boss-09", "后端工程师", "示例城市", "描述")],
            contacts=contacts,
        )
        prepared = json.loads(
            self.ok(
                "prepare", "--config", str(self.config_path), "--fixtures", str(fixtures),
                "--now", NOW_DAY1,
            )
        )
        self.assertEqual(prepared["accepted"], 0)
        table = self.today("--day", "2026-03-02")
        self.assertIn("平台已有联系记录", table)

    def test_d12_cli_shows_decision_greeting_and_evidence(self):
        prepared, _ = self.prepare([custom("job-full1", description=LONG_JD)])
        _, document = self.packet(prepared["run_id"])
        greeting = "您好，我有五年后端经验，做过订单系统数据迁移，想应聘这个岗位。"
        results = support.model_results(document, greetings={"job-full1": greeting})
        results_path = support.write_json(self.tmp / "results.json", results)
        self.ok(
            "decide", "--config", str(self.config_path), "--run-id", prepared["run_id"],
            "--packet", self._packet_path(prepared), "--results", str(results_path),
        )
        table = self.today("--day", "2026-03-02")
        self.assertIn("待发送", table)
        self.assertIn("入选", table)
        detail = self.today("--day", "2026-03-02", "--job", "job-full1")
        self.assertIn(greeting, detail)
        self.assertIn("完整JD结尾标记", detail)
        self.assertIn("示例城市·高新区", detail)
        self.assertIn("快照版本", detail)
        self.assertIn("条件结论", detail)
        self.assertFalse(self.marker.exists())

    def _packet_path(self, prepared):
        summary, _ = self.packet(prepared["run_id"])
        return summary["path"]


if __name__ == "__main__":
    unittest.main()
