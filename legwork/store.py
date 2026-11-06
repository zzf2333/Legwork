"""SQLite 状态：运行、岗位快照、批次、决策、发送占用与平台联系历史。

一个数据目录对应一个账号；库内仍记录 account_id，避免混用不同账号的数据。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Self

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  source TEXT NOT NULL,
  day TEXT NOT NULL,
  config_digest TEXT NOT NULL,
  created_at TEXT NOT NULL,
  prepare_state TEXT NOT NULL DEFAULT 'preparing'
);
CREATE TABLE IF NOT EXISTS jobs (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  account_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  company TEXT NOT NULL,
  boss_id TEXT NOT NULL,
  boss_name TEXT NOT NULL,
  title TEXT NOT NULL,
  salary TEXT NOT NULL,
  address TEXT NOT NULL,
  description TEXT NOT NULL,
  source_keyword TEXT NOT NULL,
  snapshot_digest TEXT NOT NULL,
  status TEXT NOT NULL,
  job_url TEXT NOT NULL DEFAULT '',
  status_reason TEXT NOT NULL DEFAULT '',
  status_detail TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, job_id)
);
CREATE INDEX IF NOT EXISTS jobs_account ON jobs(account_id, status);
CREATE TABLE IF NOT EXISTS job_sources (
  account_id TEXT NOT NULL,
  day TEXT NOT NULL,
  job_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  source_keyword TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  PRIMARY KEY (account_id, day, job_id, run_id, source_keyword)
);
CREATE TABLE IF NOT EXISTS collection_anomalies (
  anomaly_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  day TEXT NOT NULL,
  run_id TEXT NOT NULL,
  source_keyword TEXT NOT NULL,
  reason TEXT NOT NULL,
  detail TEXT NOT NULL,
  collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packets (
  packet_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  input_digest TEXT NOT NULL,
  job_ids TEXT NOT NULL,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'replaced'))
);
CREATE TABLE IF NOT EXISTS decisions (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  conditions TEXT NOT NULL,
  greeting TEXT,
  resume_fact_ids TEXT,
  result_digest TEXT NOT NULL,
  decided_at TEXT NOT NULL,
  PRIMARY KEY (run_id, job_id)
);
CREATE TABLE IF NOT EXISTS attempts (
  attempt_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  day TEXT NOT NULL,
  state TEXT NOT NULL,
  claimed_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  evidence TEXT NOT NULL,
  UNIQUE (account_id, mode, job_id)
);
CREATE TABLE IF NOT EXISTS platform_history (
  account_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  job_id TEXT NOT NULL,
  company TEXT NOT NULL,
  boss_id TEXT NOT NULL,
  source TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (account_id, mode, job_id, source)
);
"""


class StoreError(RuntimeError):
    """状态库无法满足请求。"""

    code = "store_error"


class DuplicateClaim(StoreError):
    code = "already_claimed"


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        stored = self._stored_schema_version()
        if stored is not None and stored != str(SCHEMA_VERSION):
            # 旧库不做迁移也不清空：在建表和索引之前拒绝，用户另建目录或自行备份处理。
            self.db.close()
            raise StoreError(
                f"数据目录 schema_version={stored}，本程序使用 {SCHEMA_VERSION}："
                "不自动迁移或清空，请使用新的数据目录"
            )
        self.db.executescript(SCHEMA)
        if stored is None:
            self._set_meta("schema_version", str(SCHEMA_VERSION))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- meta ---------------------------------------------------------------
    def _stored_schema_version(self) -> str | None:
        exists = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        return self._meta("schema_version") if exists else None

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, value))

    def bind_account(self, account_id: str) -> None:
        stored = self._meta("account_id")
        if stored is None:
            self._set_meta("account_id", account_id)
            self.db.commit()
        elif stored != account_id:
            raise StoreError(f"数据目录绑定账号 {stored}，与配置账号 {account_id} 不一致")

    def account_id(self) -> str | None:
        return self._meta("account_id")

    def require_mode(self, mode: str) -> str:
        """数据目录只服务一种通道：fixtures 与 live 互斥，避免模拟数据进入真实状态。"""
        stored = self._meta("mode")
        if stored is None:
            self._set_meta("mode", mode)
            self.db.commit()
            return mode
        if stored != mode:
            raise StoreError(f"数据目录绑定通道 {stored}，与当前请求的 {mode} 不一致")
        return stored

    def mode(self) -> str:
        return self._meta("mode") or ""

    # -- runs ---------------------------------------------------------------
    def create_run(
        self, run_id: str, account_id: str, source: str, day: str, config_digest: str, created_at: str
    ) -> dict | None:
        self.db.execute(
            "INSERT INTO runs (run_id, account_id, source, day, config_digest, created_at, prepare_state)"
            " VALUES (?, ?, ?, ?, ?, ?, 'preparing')",
            (run_id, account_id, source, day, config_digest, created_at),
        )
        self.db.commit()
        return self.run(run_id)

    def run(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def set_run_prepare_state(self, run_id: str, state: str) -> None:
        self.db.execute("UPDATE runs SET prepare_state = ? WHERE run_id = ?", (state, run_id))
        self.db.commit()

    def latest_run(self, account_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM runs WHERE account_id = ? ORDER BY created_at DESC, run_id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- jobs ---------------------------------------------------------------
    def add_job(
        self,
        run_id: str,
        account_id: str,
        job: dict,
        snapshot_digest: str,
        status: str,
        *,
        job_url: str = "",
        status_reason: str = "",
        status_detail: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO jobs (run_id, account_id, job_id, company, boss_id, boss_name, title, salary,"
            " address, description, source_keyword, snapshot_digest, status, job_url, status_reason,"
            " status_detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                account_id,
                job["job_id"],
                job["company"],
                job["boss_id"],
                job["boss_name"],
                job["title"],
                job["salary"],
                job["address"],
                job["description"],
                job.get("source_keyword", ""),
                snapshot_digest,
                status,
                job_url,
                status_reason,
                status_detail,
            ),
        )
        self.db.commit()

    def job(self, run_id: str, job_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM jobs WHERE run_id = ? AND job_id = ?", (run_id, job_id)
        ).fetchone()
        return dict(row) if row else None

    def jobs(self, run_id: str, status: str | None = None) -> list[dict]:
        if status is None:
            rows = self.db.execute(
                "SELECT * FROM jobs WHERE run_id = ? ORDER BY job_id", (run_id,)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM jobs WHERE run_id = ? AND status = ? ORDER BY job_id",
                (run_id, status),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_job_status(
        self, run_id: str, job_id: str, status: str, *, reason: str = "", detail: str = ""
    ) -> None:
        self.db.execute(
            "UPDATE jobs SET status = ?, status_reason = ?, status_detail = ?"
            " WHERE run_id = ? AND job_id = ?",
            (status, reason, detail, run_id, job_id),
        )
        self.db.commit()

    # -- 同日来源与采集异常 ---------------------------------------------------
    def record_source(
        self, account_id: str, day: str, job_id: str, run_id: str, source_keyword: str, collected_at: str
    ) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO job_sources (account_id, day, job_id, run_id, source_keyword,"
            " collected_at) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, day, job_id, run_id, source_keyword, collected_at),
        )
        self.db.commit()

    def add_anomaly(
        self,
        anomaly_id: str,
        account_id: str,
        day: str,
        run_id: str,
        source_keyword: str,
        reason: str,
        detail: str,
        collected_at: str,
    ) -> None:
        self.db.execute(
            "INSERT INTO collection_anomalies (anomaly_id, account_id, day, run_id, source_keyword,"
            " reason, detail, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (anomaly_id, account_id, day, run_id, source_keyword, reason, detail, collected_at),
        )
        self.db.commit()

    # -- packets ------------------------------------------------------------
    def save_packet(self, packet_id: str, run_id: str, input_digest: str, job_ids: list[str], created_at: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO packets (packet_id, run_id, input_digest, job_ids, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (packet_id, run_id, input_digest, json.dumps(job_ids), created_at),
        )
        self.db.commit()

    def replace_packet(self, packet_id: str) -> None:
        self.db.execute("UPDATE packets SET state = 'replaced' WHERE packet_id = ?", (packet_id,))
        self.db.commit()

    def packet(self, packet_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM packets WHERE packet_id = ?", (packet_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["job_ids"] = json.loads(payload["job_ids"])
        return payload

    # -- decisions ----------------------------------------------------------
    def packets_for_run(self, run_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM packets WHERE run_id = ? ORDER BY created_at DESC, packet_id DESC",
            (run_id,),
        ).fetchall()
        packets = []
        for row in rows:
            payload = dict(row)
            payload["job_ids"] = json.loads(payload["job_ids"])
            packets.append(payload)
        return packets

    def decision(self, run_id: str, job_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM decisions WHERE run_id = ? AND job_id = ?", (run_id, job_id)
        ).fetchone()
        return dict(row) if row else None

    def commit_decisions(self, run_id: str, decisions: list[dict], decided_at: str) -> None:
        """整批决策一次提交：全部成功或全部不落盘。"""
        with self.db:
            for decision in decisions:
                self.db.execute(
                    "INSERT INTO decisions (run_id, job_id, action, reason, conditions, greeting,"
                    " resume_fact_ids, result_digest, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        decision["job_id"],
                        decision["action"],
                        decision["reason"],
                        json.dumps(decision["conditions"], ensure_ascii=False),
                        decision.get("greeting"),
                        json.dumps(decision.get("resume_fact_ids") or []),
                        decision["result_digest"],
                        decided_at,
                    ),
                )
                self.db.execute(
                    "UPDATE jobs SET status = ? WHERE run_id = ? AND job_id = ?",
                    (decision["status"], run_id, decision["job_id"]),
                )

    # -- attempts -----------------------------------------------------------
    def attempt(self, account_id: str, job_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM attempts WHERE account_id = ? AND mode = ? AND job_id = ?",
            (account_id, self.mode(), job_id),
        ).fetchone()
        return dict(row) if row else None

    def claim_attempt(
        self,
        attempt_id: str,
        account_id: str,
        run_id: str,
        job_id: str,
        day: str,
        claimed_at: str,
    ) -> None:
        try:
            self.db.execute(
                "INSERT INTO attempts (attempt_id, account_id, run_id, job_id, mode, day, state,"
                " claimed_at, updated_at, evidence)"
                " VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?, ?, '{}')",
                (attempt_id, account_id, run_id, job_id, self.mode(), day, claimed_at, claimed_at),
            )
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise DuplicateClaim(f"岗位 {job_id} 已有发送占用，不重复投递") from exc

    def reopen_attempt(self, attempt_id: str, updated_at: str) -> bool:
        """把结果不明的占用重新置为进行中；只允许从 uncertain 转移，否则返回 False。"""
        cursor = self.db.execute(
            "UPDATE attempts SET state = 'sending', updated_at = ?"
            " WHERE attempt_id = ? AND state = 'uncertain'",
            (updated_at, attempt_id),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def finish_attempt(self, attempt_id: str, state: str, evidence: dict, updated_at: str) -> None:
        self.db.execute(
            "UPDATE attempts SET state = ?, evidence = ?, updated_at = ? WHERE attempt_id = ?",
            (state, json.dumps(evidence, ensure_ascii=False), updated_at, attempt_id),
        )
        self.db.commit()

    def quota_used(self, account_id: str, day: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE account_id = ? AND mode = ? AND day = ?",
            (account_id, self.mode(), day),
        ).fetchone()
        return int(row["n"])

    def attempts(self, account_id: str, day: str | None = None) -> list[dict]:
        if day is None:
            rows = self.db.execute(
                "SELECT * FROM attempts WHERE account_id = ? AND mode = ? ORDER BY claimed_at",
                (account_id, self.mode()),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM attempts WHERE account_id = ? AND mode = ? AND day = ?"
                " ORDER BY claimed_at",
                (account_id, self.mode(), day),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- platform history ---------------------------------------------------
    def record_platform_contacts(self, account_id: str, contacts: list[dict], recorded_at: str) -> None:
        for item in contacts:
            self.db.execute(
                "INSERT OR REPLACE INTO platform_history (account_id, mode, job_id, company, boss_id,"
                " source, recorded_at) VALUES (?, ?, ?, ?, ?, 'contacts', ?)",
                (account_id, self.mode(), item["job_id"], item["company"], item["boss_id"], recorded_at),
            )
        self.db.commit()

    def platform_contacts(self, account_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT job_id, company, boss_id FROM platform_history WHERE account_id = ? AND mode = ?",
            (account_id, self.mode()),
        ).fetchall()
        return [dict(row) for row in rows]

    def committed_identities(self, account_id: str, config_digest: str, day: str) -> list[dict]:
        """仍在生效的岗位身份，用于跨任务去重。

        已占用的岗位永久防重；未发送的 ready 只在同一配置的同一天有效，
        过期后不阻塞新候选，仍保留在旧运行里供人工查看。
        """
        rows = self.db.execute(
            "SELECT jobs.job_id, jobs.company, jobs.boss_id FROM jobs"
            " JOIN runs ON runs.run_id = jobs.run_id"
            " WHERE jobs.account_id = ? AND ("
            "  jobs.status IN ('sending', 'sent', 'uncertain')"
            "  OR (jobs.status = 'ready' AND runs.config_digest = ? AND runs.day = ?)"
            "  OR jobs.job_id IN (SELECT job_id FROM attempts WHERE account_id = ? AND mode = ?)"
            " )",
            (account_id, config_digest, day, account_id, self.mode()),
        ).fetchall()
        return [dict(row) for row in rows]

    def ready_jobs(self, run_id: str) -> list[dict]:
        return self.jobs(run_id, "ready")

    def has_awaiting(self, run_id: str) -> bool:
        return bool(self.jobs(run_id, "awaiting_decisions"))

    # -- projections --------------------------------------------------------
    def job_status_counts(self, account_id: str) -> dict:
        rows = self.db.execute(
            "SELECT status, COUNT(*) AS n FROM jobs WHERE account_id = ? GROUP BY status",
            (account_id,),
        ).fetchall()
        counts = {"awaiting_decisions": 0, "ready": 0, "sending": 0, "uncertain": 0, "sent": 0, "skipped": 0}
        for row in rows:
            counts[row["status"]] = int(row["n"])
        return counts

    def run_count(self, account_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE account_id = ?", (account_id,)
        ).fetchone()
        return int(row["n"])
