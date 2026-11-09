"""配置读取与校验。

所有投递口径都必须由用户配置文件显式给出，程序不内置城市、关键词、额度或话术。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
DEDUPE_SCOPES = ("job", "boss", "company")
PROGRAM_FIELDS = ("title", "description", "address")
PROGRAM_OPS = ("contains", "excludes")
UNKNOWN_POLICIES = ("skip", "allow")
PAGE_LABEL = re.compile(r"^p[1-9][0-9]*$")


class ConfigError(ValueError):
    """配置无法用于投递。"""

    code = "invalid_config"


@dataclass(frozen=True)
class Requirement:
    id: str
    kind: str
    text: str
    unknown_policy: str | None = None
    field: str | None = None
    op: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class Config:
    account_id: str
    platform_user_id: str
    city_code: str
    city_name: str
    keywords: tuple[str, ...]
    resume_facts: tuple[dict, ...]
    requirements: tuple[Requirement, ...]
    preferences: dict
    dedupe_scope: tuple[str, ...]
    daily_limit: int
    batch_size: int
    collect_per_keyword: int
    browser: dict
    timezone: str
    live: bool
    digest: str
    path: Path

    @property
    def semantic_requirements(self) -> tuple[Requirement, ...]:
        return tuple(item for item in self.requirements if item.kind == "semantic")

    @property
    def program_requirements(self) -> tuple[Requirement, ...]:
        return tuple(item for item in self.requirements if item.kind == "program")

    @property
    def activity_required(self) -> bool:
        """配置声明了「招聘方一周内活跃」这类硬条件：准备期直接排除久未活跃的岗位。"""
        return any(item.kind == "activity" for item in self.requirements)

    @property
    def resume_fact_ids(self) -> set[str]:
        return {item["id"] for item in self.resume_facts}

    def model_summary(self) -> dict:
        """交给模型的配置摘要：只含判断所需字段。"""
        return {
            "account_id": self.account_id,
            "city_code": self.city_code,
            "city_name": self.city_name,
            "keywords": list(self.keywords),
            "resume_facts": [dict(item) for item in self.resume_facts],
            "hard_requirements": [
                {
                    "id": item.id,
                    "text": item.text,
                    "unknown_policy": item.unknown_policy,
                }
                for item in self.semantic_requirements
            ],
            "preferences": self.preferences,
            "dedupe_scope": list(self.dedupe_scope),
        }

    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load(path: str | Path) -> Config:
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{path}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是合法 JSON：{path}（{exc}）") from exc
    return parse(document, path)


def parse(document: dict, path: str | Path = "<memory>") -> Config:
    path = Path(path)
    if not isinstance(document, dict):
        raise ConfigError("配置根节点必须是 JSON 对象")
    digest = _digest(document)
    _expect(document, "schema_version", SCHEMA_VERSION)

    account_id = _text(document, "account_id")
    platform_user_id = _text(document, "platform_user_id")
    if not platform_user_id.isdigit():
        raise ConfigError("platform_user_id 必须是平台账号数字标识")
    city_code = _text(document, "city_code")
    city_name = _text(document, "city_name")

    keywords = _text_list(document, "keywords")
    resume_facts = _resume_facts(document)
    requirements = _requirements(document)
    preferences = document.get("preferences", {})
    if not isinstance(preferences, dict):
        raise ConfigError("preferences 必须是 JSON 对象")

    dedupe_scope = _text_list(document, "dedupe_scope")
    for scope in dedupe_scope:
        if scope not in DEDUPE_SCOPES:
            raise ConfigError(f"dedupe_scope 只支持 {DEDUPE_SCOPES}，收到 {scope!r}")

    daily_limit = _bounded_int(document, "daily_limit", 1, 100)
    batch_size = _bounded_int(document, "batch_size", 1, 20)
    collect_per_keyword = _bounded_int(document, "collect_per_keyword", 0, 100, default=40)

    browser = document.get("browser")
    if not isinstance(browser, dict):
        raise ConfigError("browser 必须是 JSON 对象，包含 space、job_page、chat_page")
    space = browser.get("space")
    if isinstance(space, bool) or not isinstance(space, (int, str)) or str(space).strip() == "":
        raise ConfigError("browser.space 必须是 Ego 空间编号或空间名称")
    pages = {}
    for key in ("job_page", "chat_page"):
        value = browser.get(key)
        if not isinstance(value, str) or not PAGE_LABEL.match(value):
            raise ConfigError(f"browser.{key} 必须是形如 p1 的页面标签")
        pages[key] = value
    if pages["job_page"] == pages["chat_page"]:
        raise ConfigError("browser.job_page 与 browser.chat_page 不能是同一页面")

    timezone = _text(document, "timezone")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"timezone 无效：{timezone}") from exc

    live = document.get("live")
    if not isinstance(live, bool):
        raise ConfigError("live 必须是布尔值：明确是否允许操作真实平台")

    return Config(
        account_id=account_id,
        platform_user_id=platform_user_id,
        city_code=city_code,
        city_name=city_name,
        keywords=tuple(keywords),
        resume_facts=tuple(resume_facts),
        requirements=tuple(requirements),
        preferences=preferences,
        dedupe_scope=tuple(dedupe_scope),
        daily_limit=daily_limit,
        batch_size=batch_size,
        collect_per_keyword=collect_per_keyword,
        browser={**browser, **pages},
        timezone=timezone,
        live=live,
        digest=digest,
        path=path,
    )


def _digest(document: dict) -> str:
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expect(document: dict, key: str, expected) -> None:
    value = document.get(key)
    if value != expected:
        raise ConfigError(f"{key} 必须是 {expected!r}，收到 {value!r}")


def _text(document: dict, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} 必须是非空字符串")
    return value.strip()


def _text_list(document: dict, key: str) -> list[str]:
    value = document.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{key} 必须是非空字符串数组")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{key} 必须是非空字符串数组")
    return [item.strip() for item in value]


def _bounded_int(document: dict, key: str, low: int, high: int, *, default: int | None = None) -> int:
    value = document.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{key} 必须是 {low}..{high} 之间的整数")
    return value


def _resume_facts(document: dict) -> list[dict]:
    value = document.get("resume_facts")
    if not isinstance(value, list) or not value:
        raise ConfigError("resume_facts 必须是非空数组，每项含 id 与 text")
    facts = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError("resume_facts 每项必须是对象")
        fact_id = item.get("id")
        text = item.get("text")
        if not isinstance(fact_id, str) or not fact_id.strip():
            raise ConfigError("resume_facts 每项必须有非空 id")
        if fact_id in seen:
            raise ConfigError(f"resume_facts 的 id 重复：{fact_id}")
        if not isinstance(text, str) or not text.strip():
            raise ConfigError(f"resume_facts.{fact_id} 必须有非空 text")
        seen.add(fact_id)
        facts.append({"id": fact_id, "text": text.strip()})
    return facts


def _requirements(document: dict) -> list[Requirement]:
    value = document.get("hard_requirements")
    if not isinstance(value, list) or not value:
        raise ConfigError("hard_requirements 必须是非空数组")
    items = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ConfigError("hard_requirements 每项必须是对象")
        requirement_id = raw.get("id")
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise ConfigError("hard_requirements 每项必须有非空 id")
        if requirement_id in seen:
            raise ConfigError(f"hard_requirements 的 id 重复：{requirement_id}")
        seen.add(requirement_id)
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ConfigError(f"hard_requirements.{requirement_id} 必须有非空 text")
        kind = raw.get("kind")
        if kind == "program":
            field = raw.get("field")
            op = raw.get("op")
            expected = raw.get("value")
            if field not in PROGRAM_FIELDS:
                raise ConfigError(f"hard_requirements.{requirement_id}.field 只支持 {PROGRAM_FIELDS}")
            if op not in PROGRAM_OPS:
                raise ConfigError(f"hard_requirements.{requirement_id}.op 只支持 {PROGRAM_OPS}")
            if not isinstance(expected, str) or not expected.strip():
                raise ConfigError(f"hard_requirements.{requirement_id}.value 必须是非空字符串")
            items.append(
                Requirement(
                    id=requirement_id,
                    kind="program",
                    text=text.strip(),
                    field=field,
                    op=op,
                    value=expected.strip(),
                )
            )
        elif kind == "semantic":
            policy = raw.get("unknown_policy")
            if policy not in UNKNOWN_POLICIES:
                raise ConfigError(
                    f"hard_requirements.{requirement_id}.unknown_policy 必须是 skip 或 allow"
                )
            items.append(
                Requirement(
                    id=requirement_id,
                    kind="semantic",
                    text=text.strip(),
                    unknown_policy=policy,
                )
            )
        elif kind == "activity":
            # 活跃时间由程序按采集文本判断；只接受 id/kind/text，其他键一律拒绝。
            extra = sorted(set(raw) - {"id", "kind", "text"})
            if extra:
                raise ConfigError(
                    f"hard_requirements.{requirement_id}.{extra[0]} 不能用于 activity 条件"
                )
            items.append(Requirement(id=requirement_id, kind="activity", text=text.strip()))
        else:
            raise ConfigError(
                f"hard_requirements.{requirement_id}.kind 只支持 program、semantic 或 activity"
            )
    if not any(item.kind == "semantic" for item in items):
        raise ConfigError("hard_requirements 至少需要一个交给 agent 判断的 semantic 条件")
    return items
