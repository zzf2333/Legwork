"""今日投递清单：按账号、配置时区日期和岗位 ID 汇总既有快照、决策与占用。

只读投影，不复制完整数据：一岗一行，同日多来源合并可追溯，不同岗位即使同公司也分开。
"""
from __future__ import annotations

import json
import unicodedata

from .errors import PolicyError

STATUS_LABELS = {
    "claimed": "发送中",
    "sent": "已发送",
    "uncertain": "结果待核对",
    "ready": "待发送",
    "skipped": "判断跳过",
    "awaiting_decisions": "待判断",
    "excluded": "采集排除",
    "stale": "待重新准备",
}

REASON_LABELS = {
    "program_requirement": "不满足程序条件",
    "missing_detail": "缺少字段",
    "duplicate_in_batch": "同批次重复",
    "platform_history": "平台已有联系记录",
    "prior_run": "已进入既往任务或已占用",
    "not_first_contact": "平台显示不是首次沟通入口",
    "inactive_recruiter": "招聘方活跃时间超过一周",
    "budget_exhausted": "本轮额度不足，未安排判断",
    "awaiting_preparation": "待后续准备",
    "snapshot_changed": "JD 已变化，需重新准备",
    "config_changed": "配置已变化，需重新准备",
    "reused_decision": "复用同日已接收结果",
    "collection_error": "采集信息无法核实",
    "observation_incomplete": "最新观察无法确认旧计划",
}


# -- 查询 -------------------------------------------------------------------
def day_jobs(store, account_id: str, day: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT jobs.*, runs.day AS run_day, runs.config_digest AS config_digest,"
        " runs.created_at AS run_created_at, runs.source AS run_source,"
        " runs.prepare_state AS prepare_state, runs.rowid AS run_order FROM jobs JOIN runs ON runs.run_id = jobs.run_id"
        " WHERE jobs.account_id = ? AND runs.day = ? ORDER BY runs.created_at DESC, runs.rowid DESC, jobs.job_id",
        (account_id, day),
    ).fetchall()
    return [dict(row) for row in rows]


def day_sources(store, account_id: str, day: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT job_id, run_id, source_keyword, collected_at FROM job_sources"
        " WHERE account_id = ? AND day = ? ORDER BY collected_at, run_id",
        (account_id, day),
    ).fetchall()
    return [dict(row) for row in rows]


def anomalies(store, account_id: str, day: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM collection_anomalies WHERE account_id = ? AND day = ?"
        " ORDER BY collected_at, anomaly_id",
        (account_id, day),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_day_config_digest(store, account_id: str, day: str) -> str | None:
    row = store.db.execute(
        "SELECT config_digest FROM runs WHERE account_id = ? AND day = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (account_id, day),
    ).fetchone()
    return row["config_digest"] if row else None


# -- 准备阶段复用的查询 -----------------------------------------------------
def valid_today_decision(
    store, account_id: str, day: str, job_id: str, config_digest: str, snapshot: str
) -> dict | None:
    """同日同配置、同 JD 且未占用的已接收结果：直接复用，不再调用模型。"""
    row = store.db.execute(
        "SELECT jobs.run_id AS run_id, jobs.status AS status, decisions.* FROM decisions"
        " JOIN jobs ON jobs.run_id = decisions.run_id AND jobs.job_id = decisions.job_id"
        " JOIN runs ON runs.run_id = jobs.run_id"
        " WHERE jobs.account_id = ? AND runs.day = ? AND jobs.job_id = ?"
        " AND runs.config_digest = ? AND jobs.snapshot_digest = ?"
        " AND jobs.status IN ('ready', 'skipped')"
        " AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.account_id = jobs.account_id"
        "  AND attempts.mode = ? AND attempts.job_id = jobs.job_id)"
        " ORDER BY decisions.decided_at DESC LIMIT 1",
        (account_id, day, job_id, config_digest, snapshot, store.mode()),
    ).fetchone()
    return dict(row) if row else None


def day_ready_snapshots(store, account_id: str, day: str, job_id: str, config_digest: str) -> set[str]:
    """同日同配置、尚未占用的 ready 快照；用于判断可复用还是需要重新准备。"""
    rows = store.db.execute(
        "SELECT snapshot_digest FROM jobs JOIN runs ON runs.run_id = jobs.run_id"
        " WHERE jobs.account_id = ? AND runs.day = ? AND jobs.job_id = ?"
        " AND runs.config_digest = ? AND jobs.status = 'ready'"
        " AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.account_id = jobs.account_id"
        "  AND attempts.mode = ? AND attempts.job_id = jobs.job_id)",
        (account_id, day, job_id, config_digest, store.mode()),
    ).fetchall()
    return {row["snapshot_digest"] for row in rows}


def supersede_stale_ready(
    store, account_id: str, day: str, job_id: str, config_digest: str, snapshot: str,
    *, issue=None, trusted=True,
) -> None:
    """新的观察先使未占用的旧计划失效；缺字段不覆盖原完整快照。"""
    rows = store.db.execute(
        "SELECT jobs.run_id, jobs.snapshot_digest, runs.config_digest FROM jobs"
        " JOIN runs ON runs.run_id = jobs.run_id"
        " WHERE jobs.account_id = ? AND jobs.job_id = ? AND runs.day = ? AND jobs.status = 'ready'"
        " AND NOT EXISTS (SELECT 1 FROM attempts WHERE attempts.account_id = jobs.account_id"
        " AND attempts.mode = ? AND attempts.job_id = jobs.job_id)",
        (account_id, job_id, day, store.mode()),
    ).fetchall()
    for row in rows:
        if row["config_digest"] != config_digest:
            reason = "config_changed"
        elif trusted and row["snapshot_digest"] != snapshot:
            reason = "snapshot_changed"
        elif issue:
            reason = "observation_incomplete"
        else:
            continue
        store.set_job_status(row["run_id"], job_id, "stale", reason=reason)


# -- 投影 -------------------------------------------------------------------
def build(store, config, day: str) -> dict:
    account_id = config.account_id if config is not None else store.account_id()
    if account_id is None:
        raise PolicyError("store_unbound", "数据目录还没有绑定账号：先执行 prepare")
    active = config.digest if config is not None else latest_day_config_digest(store, account_id, day)
    sources: dict[str, list[dict]] = {}
    for item in day_sources(store, account_id, day):
        sources.setdefault(item["job_id"], []).append(
            {
                "run_id": item["run_id"],
                "source_keyword": item["source_keyword"],
                "collected_at": item["collected_at"],
            }
        )
    groups: dict[str, list[dict]] = {}
    for row in day_jobs(store, account_id, day):
        groups.setdefault(row["job_id"], []).append(row)

    rows = []
    for job_id in sorted(groups):
        attempt = store.attempt(account_id, job_id)
        if attempt is not None:
            chosen = store.job(attempt["run_id"], job_id)
            original_run = store.run(attempt["run_id"])
            if chosen is None or original_run is None:
                raise PolicyError("missing_attempt_snapshot", "发送占用的原快照缺失，不能用新观察替代")
            chosen = chosen | {"config_digest": original_run["config_digest"]}
        else:
            chosen = _choose(groups[job_id])
        decision = store.decision(chosen["run_id"], job_id)
        status, status_reason = _status(chosen, attempt, active)
        if status == "awaiting_decisions":
            expired = _expired(groups[job_id], active)
            if expired is not None:
                status_reason = expired
        rows.append(
            {
                "job_id": job_id,
                "title": chosen["title"],
                "company": chosen["company"],
                "boss_name": chosen["boss_name"],
                "boss_id": chosen["boss_id"],
                "salary": chosen["salary"],
                "address": chosen["address"],
                "job_url": chosen["job_url"],
                "description": chosen["description"],
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "verdict": _verdict(status, decision),
                "reason": _reason(status, status_reason, chosen["status_detail"], decision),
                "greeting": (decision or {}).get("greeting") or "",
                "conditions": _conditions(decision),
                "version": chosen["snapshot_digest"],
                "run_id": chosen["run_id"],
                "source_keyword": chosen["source_keyword"],
                "sources": sources.get(job_id, []),
                "attempt": _attempt_view(attempt),
                "decision": _decision_view(decision),
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        label = STATUS_LABELS.get(status, status)
        counts[label] = counts.get(label, 0) + 1
    return {
        "account_id": account_id,
        "day": day,
        "active_config_digest": active,
        "counts": counts,
        "rows": rows,
        "anomalies": [
            {
                "reason": item["reason"],
                "detail": item["detail"],
                "source_keyword": item["source_keyword"],
                "collected_at": item["collected_at"],
            }
            for item in anomalies(store, account_id, day)
        ],
    }


def _expired(rows: list[dict], active) -> str | None:
    """同日已有结果因配置或 JD 变化失效：再次准备时说明原因，不静默丢弃。"""
    for row in rows:
        if row["status"] == "stale":
            return row["status_reason"]
        if row["status"] in ("ready", "skipped") and row["config_digest"] != active:
            return "config_changed"
    return None


def _choose(rows: list[dict]) -> dict:
    return max(rows, key=lambda row: (row["run_created_at"], row["run_order"]))


def _status(row: dict, attempt, active) -> tuple[str, str]:
    if attempt is not None and attempt["run_id"] == row["run_id"]:
        return attempt["state"], ""
    if row["status"] in ("ready", "skipped") and row["config_digest"] != active:
        return "stale", "config_changed"
    return row["status"], row["status_reason"]


def _verdict(status: str, decision) -> str:
    if decision is None or status == "stale":
        return {
            "excluded": "排除",
            "stale": "待重新准备",
            "awaiting_decisions": "待判断",
        }.get(status, "待判断")
    return "入选" if decision["action"] == "send" else "排除"


def _reason(status: str, status_reason: str, status_detail: str, decision) -> str:
    if status in ("ready", "sending", "sent", "uncertain", "skipped") and decision is not None:
        return decision["reason"]
    label = REASON_LABELS.get(status_reason, "")
    return label + (f"：{status_detail}" if status_detail and label else "")


def _conditions(decision) -> list[dict]:
    if decision is None or not decision.get("conditions"):
        return []
    try:
        payload = json.loads(decision["conditions"])
    except (TypeError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _decision_view(decision) -> dict | None:
    if decision is None:
        return None
    return {
        "action": decision["action"],
        "reason": decision["reason"],
        "decided_at": decision["decided_at"],
        "result_digest": decision["result_digest"],
        "resume_fact_ids": json.loads(decision.get("resume_fact_ids") or "[]"),
    }


def _attempt_view(attempt) -> dict | None:
    if attempt is None:
        return None
    try:
        evidence = json.loads(attempt["evidence"])
    except (TypeError, json.JSONDecodeError):
        evidence = {}
    return {
        "state": attempt["state"],
        "day": attempt["day"],
        "claimed_at": attempt["claimed_at"],
        "updated_at": attempt["updated_at"],
        "evidence": evidence,
    }


# -- 文本展示 ---------------------------------------------------------------
def render(payload: dict, job_id: str | None = None) -> str:
    if job_id:
        return _render_detail(payload, job_id)
    return _render_table(payload)


def _render_table(payload: dict) -> str:
    header = ("职位／公司", "判断", "招呼摘要", "状态", "岗位链接")
    lines = [
        "今日投递清单 {day}（{account}）".format(
            day=payload["day"], account=payload["account_id"]
        )
        + "：{n} 条".format(n=len(payload["rows"])),
    ]
    if payload["counts"]:
        lines.append(
            "  " + "／".join(f"{label} {count}" for label, count in sorted(payload["counts"].items()))
        )
    if payload["anomalies"]:
        lines.append("  采集异常 {n} 条（不可发送）".format(n=len(payload["anomalies"])))
        for item in payload["anomalies"]:
            lines.append(f"    - {item['reason']}：{item['detail']}（关键词：{item['source_keyword'] or '未知'}）")
    lines.append("")
    body = [
        (
            "{title}／{company}".format(title=row["title"] or "未知", company=row["company"] or "未知"),
            row["verdict"] + ("：" + row["reason"] if row["reason"] else ""),
            _summarize(row["greeting"]) or "无",
            row["status_label"],
            row["job_url"] or "无",
        )
        for row in payload["rows"]
    ]
    widths = (
        [max(_width(cell) for cell in column) for column in zip(header, *body, strict=True)]
        if body
        else [_width(cell) for cell in header]
    )
    for cells in [header, *body]:
        lines.append(
            "  ".join(_pad(cell, width) for cell, width in zip(cells, widths, strict=True)).rstrip()
        )
    if payload["rows"]:
        lines.append("")
        lines.append("查看单岗完整信息：legwork today --job <job_id>")
    return "\n".join(lines)


def _render_detail(payload: dict, job_id: str) -> str:
    row = next((item for item in payload["rows"] if item["job_id"] == job_id), None)
    if row is None:
        raise PolicyError("unknown_job", f"今日清单没有这个岗位：{job_id}")
    lines = [
        "岗位：{title}".format(title=row["title"] or "未知"),
        "公司：{company}".format(company=row["company"] or "未知"),
        "招聘者：{name}".format(name=row["boss_name"] or "未知"),
        "薪资：{salary}".format(salary=row["salary"] or "未知"),
        "办公地址：{address}".format(address=row["address"] or "未知"),
        "岗位详情链接：{url}".format(url=row["job_url"] or "无"),
        "岗位 ID：{job_id}（快照版本 {version}）".format(
            job_id=row["job_id"], version=row["version"]
        ),
        "状态：{label}（{status}）".format(label=row["status_label"], status=row["status"]),
        "判断：{verdict}".format(verdict=row["verdict"]),
        "原因：{reason}".format(reason=row["reason"] or "无"),
    ]
    if row["conditions"]:
        lines.append("条件结论：")
        for condition in row["conditions"]:
            lines.append(
                "  - {id}：{status}；{evidence}".format(
                    id=condition.get("id", "?"),
                    status=condition.get("status", "?"),
                    evidence=condition.get("evidence") or "无证据",
                )
            )
    lines.append("保存的招呼正文：")
    lines.append(row["greeting"] or "无")
    lines.append("岗位原文（完整 JD）：")
    lines.append(row["description"] or "无")
    decision = row["decision"]
    if decision is not None:
        lines.append(
            "结论版本：{digest}（{time}）".format(
                digest=decision["result_digest"], time=decision["decided_at"]
            )
        )
    attempt = row["attempt"]
    if attempt is not None:
        lines.append(
            "执行结果：{state}，占用日 {day}，更新于 {updated}".format(
                state=attempt["state"], day=attempt["day"], updated=attempt["updated_at"]
            )
        )
        if attempt["evidence"]:
            lines.append(
                "执行证据：" + json.dumps(attempt["evidence"], ensure_ascii=False, sort_keys=True)
            )
    if row["sources"]:
        lines.append("今日来源：")
        for source in row["sources"]:
            lines.append(
                "  - {keyword}（任务 {run_id}，{time}）".format(
                    keyword=source["source_keyword"] or "未知关键词",
                    run_id=source["run_id"],
                    time=source["collected_at"],
                )
            )
    return "\n".join(lines)


def _summarize(text: str, limit: int = 18) -> str:
    flat = " ".join(str(text or "").split())
    return flat if _width(flat) <= limit else flat[:limit] + "…"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in str(text))


def _pad(text: str, width: int) -> str:
    return str(text) + " " * max(width - _width(text), 0)
