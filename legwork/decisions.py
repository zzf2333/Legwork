"""决策接收：一次性校验完整批次，原子落盘，重复提交结果不改写已执行项。"""
from __future__ import annotations

import hashlib
import json

from . import workflow
from .errors import PolicyError
from .records import read_json


def accept_decisions(store, config, run, packet_path, results_path, now) -> dict:
    run_id = run["run_id"]
    workflow.validate_preparation(store, config, run, now)
    packet = read_json(packet_path, "批次文件")
    results = read_json(results_path, "模型结果")
    packet_id = packet.get("packet_id")
    stored = store.packet(packet_id) if isinstance(packet_id, str) else None
    if stored is None or stored["run_id"] != run_id:
        raise PolicyError("packet_unknown", "批次不属于该运行：重新导出批次后再提交")
    if stored["state"] != "open":
        raise PolicyError("packet_replaced", "批次已因预算变化被替代，不能接收旧结果")
    if results.get("packet_id") != packet_id:
        raise PolicyError("packet_mismatch", "结果里的 packet_id 与批次文件不一致")

    items = results.get("results")
    if not isinstance(items, list):
        raise PolicyError("invalid_result", "results 必须是岗位结果数组")
    expected = stored["job_ids"]
    jobs = [store.job(run_id, job_id) for job_id in expected]
    if any(job is None for job in jobs):
        raise PolicyError("packet_unknown", "批次里的岗位已不在状态库中：重新导出批次")
    jobs = workflow.with_first_collected_day(store, run["account_id"], jobs)
    rebuild = workflow.packet_document(config, run, jobs)
    if stored["input_digest"] != rebuild["input_digest"]:
        raise PolicyError("packet_input_mismatch", "状态库中的批次输入与当前快照或配置不一致")
    packet_body = dict(packet)
    rebuild_body = dict(rebuild) | {"packet_id": packet_id}
    if packet_body != rebuild_body:
        raise PolicyError(
            "packet_input_mismatch",
            "批次文件内容与状态库快照、简历事实或配置不一致：重新导出批次",
        )
    by_job: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            raise PolicyError("invalid_result", "岗位结果必须是对象")
        job_id = item.get("job_id")
        if not isinstance(job_id, str) or job_id not in expected:
            raise PolicyError("unknown_job", f"结果包含本批次之外的岗位：{job_id!r}")
        if job_id in by_job:
            raise PolicyError("duplicate_job_result", f"同一岗位出现多条结果：{job_id}")
        by_job[job_id] = item
    missing = [job_id for job_id in expected if job_id not in by_job]
    if missing:
        raise PolicyError("incomplete_batch", "缺少岗位结果：" + "、".join(missing))

    pending: list[dict] = []
    duplicates = 0
    for job_id in expected:
        job = store.job(run_id, job_id)
        decision = validate_result(config, job, by_job[job_id])
        existing = store.decision(run_id, job_id)
        if existing is not None:
            if existing["result_digest"] != decision["result_digest"]:
                if store.attempt(config.account_id, job_id) is not None:
                    raise PolicyError("already_executed", f"岗位 {job_id} 已执行，不能改写结果")
                raise PolicyError("decision_conflict", f"岗位 {job_id} 已接收过不同结果")
            duplicates += 1
            continue
        if job["status"] != "awaiting_decisions":
            raise PolicyError("already_decided", f"岗位 {job_id} 不是待决策状态")
        pending.append(
            decision
            | {"job_id": job_id, "status": "ready" if decision["action"] == "send" else "skipped"}
        )
    if sum(item["action"] == "send" for item in pending) > workflow.remaining_budget(store, config, run):
        raise PolicyError("prepare_budget_exceeded", "新增话术超过当前剩余准备预算，整批拒绝，请重新导出")
    if pending:
        store.commit_decisions(run_id, pending, now.isoformat())
    if not store.has_awaiting(run_id) or workflow.remaining_budget(store, config, run) == 0:
        # 没有剩余待判断岗位：本次准备完成，可以进入发送阶段。
        workflow.finish_prepare(store, config, run, now, reason="awaiting_preparation")
    return {
        "packet_id": packet_id,
        "accepted": len(pending),
        "duplicate": duplicates == len(expected),
        "ready": sum(1 for item in pending if item["status"] == "ready"),
        "skipped": sum(1 for item in pending if item["status"] == "skipped"),
    }


def validate_result(config, job, item: dict) -> dict:
    """按配置校验单个岗位结果；send 不得放过 unmet 或 skip 策略下的 unknown。"""
    action = item.get("action")
    if action not in ("send", "skip"):
        raise PolicyError("invalid_result", f"岗位 {job['job_id']} 的 action 必须是 send 或 skip")
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise PolicyError("invalid_result", f"岗位 {job['job_id']} 缺少 reason")
    conditions = item.get("conditions")
    if not isinstance(conditions, list):
        raise PolicyError("invalid_result", f"岗位 {job['job_id']} 缺少硬条件判断")

    requirements = {requirement.id: requirement for requirement in config.semantic_requirements}
    statuses: dict[str, str] = {}
    for condition in conditions:
        if not isinstance(condition, dict):
            raise PolicyError("invalid_result", f"岗位 {job['job_id']} 的条件项必须是对象")
        condition_id = condition.get("id")
        if not isinstance(condition_id, str) or condition_id not in requirements:
            raise PolicyError(
                "invalid_conditions", f"岗位 {job['job_id']} 出现未知条件：{condition_id!r}"
            )
        if condition_id in statuses:
            raise PolicyError(
                "invalid_conditions", f"岗位 {job['job_id']} 的条件重复：{condition_id}"
            )
        status = condition.get("status")
        if status not in ("met", "unmet", "unknown"):
            raise PolicyError(
                "invalid_conditions",
                f"岗位 {job['job_id']} 的条件 {condition_id} 状态非法：{status!r}",
            )
        statuses[condition_id] = status
    missing = [condition_id for condition_id in requirements if condition_id not in statuses]
    if missing:
        raise PolicyError(
            "invalid_conditions", f"岗位 {job['job_id']} 缺少条件判断：" + "、".join(missing)
        )

    decision = {
        "action": action,
        "reason": reason.strip(),
        "conditions": [
            {
                "id": condition["id"],
                "status": condition["status"],
                "evidence": str(condition.get("evidence") or ""),
            }
            for condition in conditions
        ],
    }
    if action == "send":
        for condition_id, status in statuses.items():
            requirement = requirements[condition_id]
            if status == "unmet":
                raise PolicyError(
                    "hard_requirement_unmet",
                    f"岗位 {job['job_id']} 的硬条件 {condition_id} 判定为 unmet，不能发送",
                )
            if status == "unknown" and requirement.unknown_policy == "skip":
                raise PolicyError(
                    "hard_requirement_unknown",
                    f"岗位 {job['job_id']} 的硬条件 {condition_id} 未知且策略为 skip，不能发送",
                )
        greeting = item.get("greeting")
        if not isinstance(greeting, str) or not greeting.strip():
            raise PolicyError("invalid_greeting", f"岗位 {job['job_id']} 缺少招呼正文")
        fact_ids = item.get("resume_fact_ids")
        if not isinstance(fact_ids, list) or not fact_ids:
            raise PolicyError("unknown_resume_fact", f"岗位 {job['job_id']} 的招呼未引用简历事实")
        unknown = [fact_id for fact_id in fact_ids if fact_id not in config.resume_fact_ids]
        if unknown:
            raise PolicyError(
                "unknown_resume_fact",
                f"岗位 {job['job_id']} 引用了不存在的简历事实：" + "、".join(map(str, unknown)),
            )
        decision["greeting"] = greeting.strip()
        decision["resume_fact_ids"] = list(dict.fromkeys(fact_ids))
    decision["result_digest"] = hashlib.sha256(
        json.dumps(
            {"result": decision, "snapshot": job["snapshot_digest"]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return decision
