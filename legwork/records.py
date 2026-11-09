"""岗位快照、保存链接与身份键的共享定义。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .errors import PolicyError

JOB_FIELDS = (
    "job_id",
    "company",
    "boss_id",
    "boss_name",
    "title",
    "salary",
    "address",
    "description",
)

# 采集到的全部观察字段：快照字段之外加上只用于准备期排除与取批排序的招聘方活跃时间和公司规模。
# 这两项每天变化，不进快照摘要，也不进发送前的 JD 比对。
OBSERVED_FIELDS = JOB_FIELDS + ("boss_active", "company_size")

# 发送前比对用的字段：直接打开详情页读不到地址（.location-address 与 _jobInfo.address 都不存在），
# 地址仍照常采集、参与程序条件，只是不进发送前的 JD 比对。
SEND_SNAPSHOT_FIELDS = tuple(field for field in JOB_FIELDS if field != "address")

# 平台给出的活跃文本只有这几种，分成一周内与一周外；两份清单之外的算未知，未知不排除也不优先。
RECENT_ACTIVITY = frozenset({"在线", "刚刚活跃", "今日活跃", "3日内活跃", "本周活跃"})
STALE_ACTIVITY = frozenset({"2周内活跃", "本月活跃", "3月内活跃", "半年前活跃", "近半年活跃"})

SIZE_ORDER = {"中小": 0, "中大型": 1, "大型": 2, "": 3}
ACTIVITY_ORDER = {"一周内": 0, "": 1, "一周外": 2}

# 稳定岗位 ID 只允许平台加密 ID 字符集；带 / ? # 等字符的值拼不出可信链接。
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,127}$")
JOB_URL = "https://www.zhipin.com/job_detail/{job_id}.html"


def normalize_text(value) -> str:
    """只统一空白：详情页与列表页的缩进、换行差异不算 JD 变化，真实内容一律保留。"""
    return " ".join(str(value or "").split())


def _digest(job: dict, fields: tuple[str, ...]) -> str:
    canonical = json.dumps(
        {field: normalize_text(job.get(field, "")) for field in fields},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def snapshot_digest(job: dict) -> str:
    """岗位快照摘要：绑定 JD 正文内容，用于同日判定复用与占用比对。

    job_url 与 source_keyword 是定位和来源信息，不参与正文摘要。
    """
    return _digest(job, JOB_FIELDS)


def send_digest(job: dict) -> str:
    """发送前比对用的摘要：只含直接详情页也能读到的字段，不含地址。"""
    return _digest(job, SEND_SNAPSHOT_FIELDS)


def activity_bucket(text) -> str:
    """招聘方活跃文本分档：「一周内」「一周外」；平台没给或认不出时为空。"""
    value = normalize_text(text)
    if value in RECENT_ACTIVITY:
        return "一周内"
    if value in STALE_ACTIVITY:
        return "一周外"
    return ""


def company_size_bucket(text) -> str:
    """公司规模按人数上限分档：500-999 人算中小，1000-9999 算中大型，上万算大型。"""
    numbers = [
        float(number) * (10000 if unit else 1)
        for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(万)?", str(text or ""))
    ]
    if not numbers:
        return ""
    upper = max(numbers)
    if upper <= 999:
        return "中小"
    if upper <= 9999:
        return "中大型"
    return "大型"


def preference_key(job: dict) -> tuple:
    """取批顺序：先中小公司，同规模内先最近活跃，最后按岗位 ID 保持同日顺序稳定。"""
    return (
        SIZE_ORDER.get(company_size_bucket(job.get("company_size")), 3),
        ACTIVITY_ORDER.get(activity_bucket(job.get("boss_active")), 1),
        str(job.get("job_id", "")),
    )


def job_url(job_id: str) -> str:
    """保存的岗位详情地址；只接受稳定 ID，外部传入的任意 URL 在这里被拒绝。"""
    value = str(job_id or "")
    if not JOB_ID.match(value):
        raise PolicyError(
            "invalid_job_id", f"岗位 ID 不是稳定标识，不能生成保存链接：{value!r}"
        )
    return JOB_URL.format(job_id=value)


def identity_keys(config, item: dict) -> set[tuple[str, str]]:
    values = {
        "job": str(item.get("job_id", "")),
        "boss": str(item.get("boss_id", "")),
        "company": str(item.get("company", "")),
    }
    return {(scope, values[scope]) for scope in config.dedupe_scope}


def read_json(path, label: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError("invalid_input", f"无法读取{label}：{path}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError("invalid_input", f"{label}不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(payload, dict):
        raise PolicyError("invalid_input", f"{label}必须是 JSON 对象")
    return payload
