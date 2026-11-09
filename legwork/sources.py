"""候选输入来源：离线 fixture 与真实平台采集。

两个来源返回同一种快照形状：
{"jobs": [岗位快照...], "contacts": [{"job_id", "company", "boss_id"}], "history_complete": bool}
"""
from __future__ import annotations

import json
from pathlib import Path

from .errors import PolicyError
from .records import OBSERVED_FIELDS


class FixtureSource:
    mode = "fixtures"
    """离线输入：jobs.json 与 contacts.json。不接触浏览器，不能发送消息。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def collect(self, config, now) -> dict:
        jobs = _read_json(self.path / "jobs.json")
        contacts = _read_json(self.path / "contacts.json")
        if not isinstance(jobs, list):
            raise PolicyError("fixture_invalid", "fixtures/jobs.json 必须是岗位数组")
        if not isinstance(contacts, dict):
            raise PolicyError("fixture_invalid", "fixtures/contacts.json 必须是对象")
        platform_user_id = str(contacts.get("platform_user_id", ""))
        if platform_user_id and platform_user_id != config.platform_user_id:
            raise PolicyError(
                "account_mismatch",
                "fixture 的平台账号与配置 platform_user_id 不一致：拒绝混用账号数据",
            )
        rows = []
        for item in contacts.get("contacts") or []:
            rows.append(
                {
                    "job_id": str(item.get("job_id", "")),
                    "company": str(item.get("company", "")),
                    "boss_id": str(item.get("boss_id", "")),
                }
            )
        return {
            "jobs": [
                {field: str(item.get(field, "") or "") for field in OBSERVED_FIELDS}
                | {
                    "can_add_friend": item.get("can_add_friend") is not False,
                    "source_keyword": str(item.get("source_keyword") or ""),
                }
                for item in jobs
            ],
            "contacts": rows,
            "history_complete": contacts.get("history_complete") is True,
            "anomalies": [],
        }


class EgoSource:
    mode = "live"
    """真实平台采集：按关键词搜索列表，逐个读取岗位详情，并读取已有联系历史。

    已完成小规模 EGO 实页采集验证；发送链路仍需独立验收。
    """

    def __init__(self, browser):
        self.browser = browser

    def collect(self, config, now) -> dict:
        chat = self.browser.load_history()
        if chat.get("history_complete") is not True:
            raise PolicyError(
                "history_incomplete",
                "平台联系历史未完整加载：先在聊天页加载全部历史，不能把空的新库当成从未联系",
            )
        jobs = []
        anomalies = []
        cache = {}
        for keyword in config.keywords:
            state = self.browser.search(keyword, config.city_code)
            for row in state.get("rows") or []:
                job_id = str(row.get("job_id") or "")
                href_id = str(row.get("href_id") or "")
                issue = None
                if not job_id or not row.get("boss_id") or not row.get("company") or not row.get("title"):
                    issue = "missing_identity"
                elif href_id != job_id:
                    issue = "link_identity_mismatch"
                target = row | {"source_keyword": keyword}
                if issue:
                    anomalies.append({"source_keyword": keyword, "reason": issue,
                                      "detail": f"岗位 {job_id or '未知'} 的列表身份或链接无法核实"})
                    if job_id:
                        jobs.append(target | {"observation_error": issue})
                    continue
                if job_id in cache:
                    previous = cache[job_id]
                    if any(previous.get(k) != row.get(k) for k in ("company", "boss_id")):
                        jobs.append(target | {"observation_error": "identity_conflict"})
                    else:
                        jobs.append(previous | {"source_keyword": keyword})
                    continue
                self.browser.open_job(target)
                card = self.browser.card_state()
                if any(card.get(key) != row.get(key) for key in ("job_id", "company", "boss_id")):
                    raise PolicyError("identity_mismatch", f"详情页身份与列表不一致：{job_id}")
                observed = {field: str(card.get(field, "") or "") for field in OBSERVED_FIELDS} | {
                    "can_add_friend": card.get("can_add_friend") is True,
                    "source_keyword": keyword,
                }
                cache[job_id] = observed
                jobs.append(observed)
        return {
            "jobs": jobs,
            "contacts": [
                {
                    "job_id": str(item.get("job_id", "")),
                    "company": str(item.get("company", "")),
                    "boss_id": str(item.get("boss_id", "")),
                }
                for item in chat.get("contacts") or []
            ],
            "history_complete": True,
            "anomalies": anomalies,
        }


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError("fixture_missing", f"读取离线输入失败：{path}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError("fixture_invalid", f"离线输入不是合法 JSON：{path}（{exc}）") from exc
