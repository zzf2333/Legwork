"""状态库表结构与版本：建表语句集中在这里，Store 只负责读写。"""

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
CREATE TABLE IF NOT EXISTS job_meta (
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  job_id TEXT NOT NULL,
  boss_active TEXT NOT NULL DEFAULT '',
  company_size TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, job_id)
);
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
