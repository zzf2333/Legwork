"""离线浏览器替身，与真实 Ego 适配分开维护。"""
from __future__ import annotations

import json
from pathlib import Path

from .ego import BrowserError, saved_job_url


class SimulatedBrowser:
    """离线替身：只按 fixture 脚本返回页面状态，从不启动 ego-browser，也不发真实消息。"""

    mode = "fixtures"

    def __init__(self, fixtures_dir: str | Path):
        root = Path(fixtures_dir)
        self._jobs = {item["job_id"]: item for item in _read_json(root / "jobs.json")}
        self._script = _read_json(root / "browser.json")
        self._job_id: str | None = None
        self._draft = ""
        self._messages: list[dict] = []
        self._selected: dict | None = None
        self._contacts = [dict(item) for item in self._script.get("contacts") or []]

    @property
    def platform_user_id(self) -> str:
        return str(self._script["platform_user_id"])

    def open_job(self, job: dict) -> dict:
        return self._open_fixture(job)

    def _open_fixture(self, job: dict) -> dict:
        job_id = job["job_id"]
        if job_id not in self._jobs:
            raise BrowserError(f"离线 fixture 缺少岗位 {job_id} 的详情")
        self._job_id = job_id
        self._draft = ""
        self._messages = []
        self._selected = None
        self._mid = 0
        return {"url": "fixture://" + job_id}

    def open_saved_job(self, job: dict) -> dict:
        url = saved_job_url(job)
        self._open_fixture(job)
        return {"url": url}

    def card_state(self) -> dict:
        job = self._job()
        return {key: job.get(key, "") for key in (
            "job_id", "company", "boss_id", "boss_name", "title", "salary", "address", "description"
        )} | {"can_add_friend": job.get("can_add_friend") is True}

    def chat_state(self) -> dict:
        return {
            "platform_user_id": self.platform_user_id,
            "history_complete": self._script.get("history_complete") is True,
            "contacts": [dict(item) for item in self._contacts],
            "selected": dict(self._selected) if self._selected else None,
            "draft": self._draft,
            "messages": [dict(item) for item in self._messages],
        }

    def click_contact(self, job: dict) -> None:
        self._fail("click_contact")
        job = self._job()
        uid = "uid-" + str(job["boss_id"])
        conversation_id = "conv-" + str(job["boss_id"])
        self._selected = {
            "job_id": job["job_id"],
            "company": job["company"],
            "boss_id": job["boss_id"],
            "uid": uid,
            "conversation_id": conversation_id,
        }
        self._messages = [
            self._message(uid, conversation_id, self._script_for(job["job_id"])["default_message"])
        ]
        self._contacts.append(
            {"job_id": job["job_id"], "company": job["company"], "boss_id": job["boss_id"]}
        )
        if self._script_for(job["job_id"]).get("reply"):
            self._messages.append(
                self._message(
                    self.platform_user_id, conversation_id, "您好，方便发一份简历吗？", sender=uid
                )
            )

    def open_contact(self, job: dict) -> dict:
        """已有会话：只选中目标会话并恢复默认招呼，不重新点击「立即沟通」。"""
        self._fail("open_contact")
        job = self._job()
        if not any(
            item.get("job_id") == job["job_id"] and item.get("boss_id") == job["boss_id"]
            for item in self._contacts
        ):
            raise BrowserError("目标会话不存在：不能补发")
        uid = "uid-" + str(job["boss_id"])
        conversation_id = "conv-" + str(job["boss_id"])
        self._selected = {
            "job_id": job["job_id"],
            "company": job["company"],
            "boss_id": job["boss_id"],
            "uid": uid,
            "conversation_id": conversation_id,
        }
        self._messages = [
            self._message(uid, conversation_id, self._script_for(job["job_id"])["default_message"])
        ]
        return {"url": "fixture://" + job["job_id"]}

    def fill_draft(self, text: str, identity: dict) -> None:
        self._fail("fill_draft")
        self._require_identity(identity)
        self._draft = text

    def click_send(self, text: str, identity: dict) -> None:
        self._fail("click_send")
        job = self._job()
        selected = self._require_identity(identity)
        self._messages.append(
            self._message(
                selected["uid"],
                selected["conversation_id"],
                text,
                delivered=self._script_for(job["job_id"]).get("delivery", True) is True,
            )
        )
        self._draft = ""

    def _message(self, recipient, conversation_id, text, *, sender=None, delivered=True):
        self._mid = getattr(self, "_mid", 0) + 1
        return {
            "mid": "mid-" + str(self._mid),
            "sender": sender or self.platform_user_id,
            "recipient": recipient,
            "conversation_id": conversation_id,
            "text": text,
            "delivered": delivered,
            "kind": "text",
        }

    def _require_identity(self, identity: dict) -> dict:
        selected = self._selected or {}
        if (
            str(selected.get("uid") or "") != str(identity.get("uid") or "")
            or str(selected.get("conversation_id") or "") != str(identity.get("conversation_id") or "")
        ):
            raise BrowserError("目标会话在操作过程中被切换")
        return selected

    def _job(self) -> dict:
        if not self._job_id:
            raise BrowserError("尚未打开岗位")
        return self._jobs[self._job_id]

    def _script_for(self, job_id: str) -> dict:
        return self._script.get("jobs", {}).get(job_id) or {
            "default_message": "",
            "reply": False,
            "fail_at": None,
        }

    def _fail(self, point: str) -> None:
        if self._script_for(self._job()["job_id"]).get("fail_at") == point:
            raise BrowserError(f"模拟中断：{point}")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
