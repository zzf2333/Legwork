"""发送执行：先持久占用，再核对并点击；结果不明立即停止本轮，保留 uncertain，不自动重发。"""
from __future__ import annotations

import uuid

from . import ego
from .errors import PolicyError, SendFailure
from .records import identity_keys, snapshot_digest
from .store import DuplicateClaim

# 出现这些结果说明平台状态已不可信，本轮必须停下，不能继续下一个岗位。
STOP_ON_REFUSAL = {
    "account_mismatch",
    "history_incomplete",
    "job_identity_changed",
    "job_snapshot_changed",
    "not_first_contact",
    "platform_history",
    "reply_present",
}


def _require_live_channel(store, config, run, browser) -> None:
    """发送与补发共同的前提：真实通道、通道一致、配置未变。"""
    if not config.live:
        raise PolicyError("live_disabled", "配置 live=false：不会操作真实平台，也不会发送消息")
    if run["source"] != browser.mode:
        raise PolicyError(
            "source_mismatch",
            f"该运行建立于 {run['source']} 通道，不能用 {browser.mode} 通道发送",
        )
    if store.mode() != browser.mode:
        raise PolicyError("source_mismatch", f"数据目录绑定 {store.mode() or '未绑定'} 通道，与发送通道不一致")
    if run["config_digest"] != config.digest:
        raise PolicyError("config_changed", "配置在运行创建后已改变：旧任务不能实发")


def send_ready(store, config, run, browser, *, clock, data_dir=None, job_ids=None) -> tuple[dict, int]:
    _require_live_channel(store, config, run, browser)
    if run.get("prepare_state") != "prepared":
        raise PolicyError(
            "prepare_incomplete",
            "本次准备还没有完成：先处理完剩余批次，或用 finalize 明确结束准备后再发送",
        )
    now = clock()
    day = now.date().isoformat()
    if run["day"] != day:
        raise PolicyError("stale_run", "旧日任务不能直接实发：用当前配置重新准备候选")
    targets = [
        job
        for job in store.jobs(run["run_id"], "ready")
        if not job_ids or job["job_id"] in job_ids
    ]
    if not targets:
        raise PolicyError("nothing_ready", "该运行没有待发送岗位")
    used = store.quota_used(config.account_id, day)
    if used >= config.daily_limit and not any(
        store.attempt(config.account_id, job["job_id"]) for job in targets
    ):
        raise PolicyError("daily_limit", f"今日首次沟通额度已用满：{used}/{config.daily_limit}")

    with ego.browser_lock(data_dir or store.path.parent):
        return _send_loop(store, config, run, browser, clock, day, targets)


def _send_loop(store, config, run, browser, clock, day, targets):
    attempted = 0
    sent = 0
    uncertain = 0
    refused: list[dict] = []
    outcomes: list[dict] = []
    stopped = None
    for job in targets:
        decision = store.decision(run["run_id"], job["job_id"])
        if decision is None or decision["action"] != "send":
            refused.append({"job_id": job["job_id"], "code": "missing_decision"})
            continue
        existing = store.attempt(config.account_id, job["job_id"])
        if existing is not None:
            refused.append(
                {"job_id": job["job_id"], "code": "already_claimed", "state": existing["state"]}
            )
            continue
        try:
            opened = _preflight(config, job, browser)
        except SendFailure as exc:
            if exc.code in ("job_identity_changed", "job_snapshot_changed"):
                reason = "snapshot_changed" if exc.code == "job_snapshot_changed" else "observation_incomplete"
                store.set_job_status(run["run_id"], job["job_id"], "stale", reason=reason, detail=str(exc))
            refused.append({"job_id": job["job_id"], "code": exc.code, "detail": str(exc)})
            if exc.code in STOP_ON_REFUSAL:
                stopped = exc.code
                break
            continue
        now = clock()
        if now.date().isoformat() != day:
            stopped = "day_rolled"
            break
        if store.quota_used(config.account_id, day) >= config.daily_limit:
            if attempted == 0:
                raise PolicyError("daily_limit", "今日首次沟通额度已用满，未发送任何消息")
            stopped = "daily_limit"
            break
        attempt_id = uuid.uuid4().hex
        try:
            store.claim_attempt(
                attempt_id, config.account_id, run["run_id"], job["job_id"], day, now.isoformat()
            )
        except DuplicateClaim:
            refused.append({"job_id": job["job_id"], "code": "already_claimed"})
            continue
        attempted += 1
        store.set_job_status(run["run_id"], job["job_id"], "sending")
        try:
            evidence = _deliver(config, job, browser, opened, decision["greeting"])
        except (SendFailure, ego.BrowserError) as exc:
            code = getattr(exc, "code", "browser_error")
            store.finish_attempt(
                attempt_id,
                "uncertain",
                {"error": {"code": code, "message": str(exc)}},
                now.isoformat(),
            )
            store.set_job_status(run["run_id"], job["job_id"], "uncertain")
            uncertain += 1
            outcomes.append({"job_id": job["job_id"], "state": "uncertain", "error": code})
            stopped = "uncertain"
            break
        store.finish_attempt(attempt_id, "sent", evidence, now.isoformat())
        store.set_job_status(run["run_id"], job["job_id"], "sent")
        sent += 1
        outcomes.append({"job_id": job["job_id"], "state": "sent"})

    payload = {
        "run_id": run["run_id"],
        "attempted": attempted,
        "sent": sent,
        "uncertain": uncertain,
        "refused": refused,
        "attempts": outcomes,
        "stopped": stopped,
    }
    if uncertain:
        return payload, 4
    if stopped == "day_rolled":
        return payload, 4
    if stopped and stopped != "daily_limit":
        return payload, 3
    if attempted == 0:
        return payload, 3
    return payload, 0


def follow_up(store, config, run, browser, *, clock, data_dir=None, job_ids=None) -> tuple[dict, int]:
    """补发：只对结果不明的岗位，向已存在的会话发一条定制正文。

    不新增首次沟通占用，也不占当日首次沟通额度；失败仍记 uncertain 并停止本轮。
    """
    _require_live_channel(store, config, run, browser)
    if not job_ids:
        raise PolicyError("followup_target_required", "补发必须用 --job 指定岗位")
    targets = [
        job for job in store.jobs(run["run_id"], "uncertain") if job["job_id"] in job_ids
    ]
    if not targets:
        raise PolicyError("nothing_uncertain", "该运行没有结果不明、可补发的岗位")
    now = clock()
    with ego.browser_lock(data_dir or store.path.parent):
        return _follow_up_loop(store, config, run, browser, now, targets)


def _follow_up_loop(store, config, run, browser, now, targets):
    sent = 0
    uncertain = 0
    refused: list[dict] = []
    outcomes: list[dict] = []
    stopped = None
    for job in targets:
        decision = store.decision(run["run_id"], job["job_id"])
        if decision is None or decision["action"] != "send":
            refused.append({"job_id": job["job_id"], "code": "missing_decision"})
            continue
        attempt = store.attempt(config.account_id, job["job_id"])
        if attempt is None:
            refused.append({"job_id": job["job_id"], "code": "no_prior_attempt"})
            continue
        if not store.reopen_attempt(attempt["attempt_id"], now.isoformat()):
            refused.append(
                {"job_id": job["job_id"], "code": "attempt_state", "state": attempt["state"]}
            )
            continue
        store.set_job_status(run["run_id"], job["job_id"], "sending")
        try:
            opened = _preflight(config, job, browser, first_contact=False)
            evidence = _deliver(config, job, browser, opened, decision["greeting"], existing=True)
        except (SendFailure, ego.BrowserError) as exc:
            code = getattr(exc, "code", "browser_error")
            store.finish_attempt(
                attempt["attempt_id"],
                "uncertain",
                {"error": {"code": code, "message": str(exc)}},
                now.isoformat(),
            )
            store.set_job_status(run["run_id"], job["job_id"], "uncertain")
            uncertain += 1
            outcomes.append({"job_id": job["job_id"], "state": "uncertain", "error": code})
            stopped = "uncertain"
            break
        store.finish_attempt(
            attempt["attempt_id"],
            "sent",
            evidence | {"kind": "follow_up", "prior_state": "uncertain"},
            now.isoformat(),
        )
        store.set_job_status(run["run_id"], job["job_id"], "sent")
        sent += 1
        outcomes.append({"job_id": job["job_id"], "state": "sent"})

    payload = {
        "run_id": run["run_id"],
        "sent": sent,
        "uncertain": uncertain,
        "refused": refused,
        "attempts": outcomes,
        "stopped": stopped,
        "quota_consumed": False,
    }
    if uncertain:
        return payload, 4
    if sent == 0:
        return payload, 3
    return payload, 0


def _preflight(config, job, browser, *, first_contact: bool = True) -> dict:
    """发送占用之前只读核对：账号、岗位身份、JD 与平台联系历史。

    这里不判断“对方已回复”：聊天页可能停在与目标无关的会话上，
    回复只在确认目标会话之后再按目标会话判断。
    """
    browser.open_saved_job(job)
    card = browser.card_state()
    if (card.get("job_id"), card.get("company"), card.get("boss_id")) != (
        job["job_id"],
        job["company"],
        job["boss_id"],
    ):
        raise SendFailure("job_identity_changed", "详情页岗位身份与快照不一致")
    if snapshot_digest(card) != job["snapshot_digest"]:
        raise SendFailure("job_snapshot_changed", "岗位描述与快照不一致：重新准备候选后再发")
    if first_contact and card.get("can_add_friend") is not True:
        raise SendFailure("not_first_contact", "平台显示该岗位不是首次沟通入口")
    chat = browser.chat_state()
    if str(chat.get("platform_user_id")) != config.platform_user_id:
        raise SendFailure("account_mismatch", "当前登录账号与配置 platform_user_id 不一致")
    if chat.get("history_complete") is not True:
        raise SendFailure("history_incomplete", "联系历史未完整加载：先加载历史再发送")
    if first_contact:
        _require_not_contacted(config, job, chat.get("contacts") or [])
    else:
        _require_contacted(config, job, chat.get("contacts") or [])
    return {"card": card}


def _deliver(config, job, browser, opened, greeting: str, *, existing: bool = False) -> dict:
    """打开目标会话，核对唯一默认消息，再填正文、发送并等待回执。

    existing=True 用于补发：会话已存在，不再点「立即沟通」。
    """
    if existing:
        browser.open_contact(job)
    else:
        browser.click_contact(job)
    chat = browser.chat_state()
    identity = _target_identity(job, chat.get("selected"))
    default = _unique_own_message(config, chat, identity)
    if _target_replied(config, chat, identity):
        raise SendFailure("reply_present", "对方已回复：停止自动发送定制正文")

    browser.fill_draft(greeting, identity)
    chat = browser.chat_state()
    _require_same_target(chat, identity)
    if chat.get("draft") != greeting:
        raise SendFailure("draft_mismatch", "草稿正文与预期不一致")
    if _target_replied(config, chat, identity):
        raise SendFailure("reply_present", "对方已回复：停止自动发送定制正文")

    browser.click_send(greeting, identity)
    chat = browser.chat_state()
    _require_same_target(chat, identity)
    receipt = _unique_custom_receipt(config, chat, identity, greeting)
    return {
        "job_id": job["job_id"],
        "snapshot_digest": job["snapshot_digest"],
        "card_digest": snapshot_digest(opened["card"]),
        "account": config.platform_user_id,
        "conversation_id": identity["conversation_id"],
        "default_message": {"mid": default["mid"], "delivered": True},
        "custom_message": {
            "mid": receipt["mid"],
            "delivered": True,
            "conversation_id": identity["conversation_id"],
        },
    }


def _target_identity(job, selected) -> dict:
    if not selected:
        raise SendFailure("conversation_mismatch", "点击后没有选中目标会话")
    if (selected.get("job_id"), selected.get("company"), selected.get("boss_id")) != (
        job["job_id"],
        job["company"],
        job["boss_id"],
    ):
        raise SendFailure("conversation_mismatch", "选中会话与目标岗位身份不一致")
    if not selected.get("uid") or not selected.get("conversation_id"):
        raise SendFailure("conversation_mismatch", "目标会话缺少 uid 或会话 ID")
    return {
        "job_id": job["job_id"],
        "company": job["company"],
        "boss_id": job["boss_id"],
        "uid": str(selected["uid"]),
        "conversation_id": str(selected["conversation_id"]),
    }


def _require_same_target(chat, identity: dict) -> None:
    selected = chat.get("selected") or {}
    if (
        str(selected.get("uid") or "") != identity["uid"]
        or str(selected.get("conversation_id") or "") != identity["conversation_id"]
        or (selected.get("job_id"), selected.get("company"), selected.get("boss_id"))
        != (identity["job_id"], identity["company"], identity["boss_id"])
    ):
        raise SendFailure("conversation_changed", "目标会话在填写或发送过程中被切换")


def _target_messages(chat, identity: dict) -> list[dict]:
    messages = []
    for message in chat.get("messages") or []:
        conversation = str(message.get("conversation_id") or "")
        if not conversation:
            raise SendFailure("message_identity_incomplete", "消息缺少会话 ID，无法核对回复或送达")
        if conversation == identity["conversation_id"]:
            messages.append(message)
    return messages


def _target_replied(config, chat, identity: dict) -> bool:
    """只有目标会话里对方发来的消息才算回复；其它会话不影响本轮。"""
    return any(
        str(message.get("sender")) == identity["uid"]
        for message in _target_messages(chat, identity)
    )


def _unique_own_message(config, chat, identity: dict) -> dict:
    own = [
        message
        for message in _target_messages(chat, identity)
        if str(message.get("sender")) == config.platform_user_id
        and str(message.get("recipient")) == identity["uid"]
        and message.get("kind") != "job_card"
    ]
    if len(own) != 1:
        raise SendFailure("default_message_missing", "未找到唯一的己方默认招呼消息")
    message = own[0]
    if message.get("delivered") is not True or not str(message.get("text") or "").strip():
        raise SendFailure("default_message_undelivered", "默认招呼未确认送达")
    if not str(message.get("mid") or ""):
        raise SendFailure("default_message_unverified", "默认招呼缺少消息 MID，无法核对唯一送达")
    return message


def _unique_custom_receipt(config, chat, identity: dict, greeting: str) -> dict:
    matches = [
        message
        for message in _target_messages(chat, identity)
        if message.get("text") == greeting
        and str(message.get("sender")) == config.platform_user_id
        and str(message.get("recipient")) == identity["uid"]
        and message.get("delivered") is True
        and str(message.get("mid") or "")
    ]
    if len(matches) != 1:
        raise SendFailure("delivery_unconfirmed", "未取得定制正文的唯一送达回执")
    return matches[0]


def _require_contacted(config, job, contacts) -> None:
    """补发必须有既有会话，否则就变成向陌生招聘者发消息。"""
    known: set[tuple[str, str]] = set()
    for item in contacts:
        known |= identity_keys(config, item)
    if not identity_keys(config, job) & known:
        raise SendFailure("contact_missing", "平台没有该岗位的联系记录：不能补发")


def _require_not_contacted(config, job, contacts) -> None:
    known: set[tuple[str, str]] = set()
    for item in contacts:
        known |= identity_keys(config, item)
    overlap = identity_keys(config, job) & known
    if overlap:
        detail = "、".join(f"{scope}={value}" for scope, value in sorted(overlap))
        raise SendFailure("platform_history", "平台已有该岗位/招聘者/公司的联系记录：" + detail)
