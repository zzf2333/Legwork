"""脱敏测试数据与真实 CLI 调用辅助。

全部岗位、公司、招聘者、账号和答话均为虚构内容，不含个人账号或真实经历。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BASE_CONFIG = {
    "schema_version": 1,
    "account_id": "acct-demo-0001",
    "platform_user_id": "100000001",
    "city_code": "101270100",
    "city_name": "示例城市",
    "keywords": ["Python 后端", "平台工程"],
    "resume_facts": [
        {"id": "rf_001", "text": "示例事实：五年后端服务开发经验。"},
        {"id": "rf_002", "text": "示例事实：负责过订单系统的数据迁移。"},
    ],
    "hard_requirements": [
        {
            "id": "hr_title_intern",
            "kind": "program",
            "field": "title",
            "op": "excludes",
            "value": "实习",
            "text": "排除实习岗位",
        },
        {
            "id": "hr_city_onsite",
            "kind": "program",
            "field": "address",
            "op": "contains",
            "value": "示例城市",
            "text": "工作地点在示例城市",
        },
        {
            "id": "hr_office",
            "kind": "semantic",
            "text": "可以接受现场办公",
            "unknown_policy": "skip",
        },
        {
            "id": "hr_night_shift",
            "kind": "semantic",
            "text": "不需要长期夜班",
            "unknown_policy": "allow",
        },
    ],
    "preferences": {"notes": "偏好平台工程方向，不做纯外包驻场。"},
    "dedupe_scope": ["job", "boss", "company"],
    "daily_limit": 5,
    "batch_size": 3,
    "browser": {"space": "legwork", "job_page": "p2", "chat_page": "p1"},
    "timezone": "Asia/Shanghai",
    "live": False,
}


def job(job_id, company, boss_id, title, address, description, salary="18-25K"):
    return {
        "job_id": job_id,
        "company": company,
        "boss_name": "招聘者" + job_id[-2:],
        "boss_id": boss_id,
        "title": title,
        "salary": salary,
        "address": address,
        "description": description,
        "can_add_friend": True,
    }


JOBS = [
    job("job-a1", "示例一号科技", "boss-01", "Python 后端工程师", "示例城市·高新区",
        "负责订单服务开发，要求五年以上 Python 经验，常驻示例城市办公室，双休。"),
    job("job-a2", "示例二号科技", "boss-02", "平台工程师", "示例城市·天府新区",
        "负责内部平台建设，需要现场办公，技术栈 Python 与 SQLite。"),
    job("job-a3", "示例三号科技", "boss-03", "后端开发工程师", "示例城市·武侯区",
        "维护交易链路服务，接受远程面试，日常在示例城市办公。"),
    job("job-a4", "示例四号科技", "boss-04", "Python 平台开发", "示例城市·锦江区",
        "搭建数据平台，需要与产品团队现场协作。"),
    job("job-intern", "示例五号科技", "boss-05", "Python 开发实习生", "示例城市·青羊区",
        "实习岗位，配合导师完成脚本开发。"),
    job("job-remote", "示例六号科技", "boss-06", "Python 后端工程师", "另一城市·示例区",
        "负责服务端开发，工作地点在另一城市，需要现场办公。"),
    job("job-dupco", "示例一号科技", "boss-07", "数据平台工程师", "示例城市·高新区",
        "同公司另一岗位，负责数据平台开发。"),
    job("job-dupbo", "示例七号科技", "boss-01", "Python 工程师", "示例城市·成华区",
        "同一招聘者的另一岗位。"),
    job("job-known", "示例九号公司", "boss-09", "后端工程师", "示例城市·金牛区",
        "平台已有联系记录的岗位。"),
]

# job-a1 在同一次搜索里重复出现一次，用于验证批内岗位去重。
JOBS.append(dict(JOBS[0]))

CONTACTS = {
    "platform_user_id": "100000001",
    "history_complete": True,
    "contacts": [{"job_id": "job-known", "company": "示例九号公司", "boss_id": "boss-09"}],
}

DEFAULT_GREETING = "您好，我对这个职位很感兴趣，方便沟通吗？"


def browser_script(overrides=None):
    jobs = {}
    for item in JOBS:
        jobs[item["job_id"]] = {
            "default_message": DEFAULT_GREETING,
            "reply": False,
            "fail_at": None,
        }
    for key, value in (overrides or {}).items():
        jobs[key].update(value)
    return {
        "platform_user_id": BASE_CONFIG["platform_user_id"],
        "history_complete": True,
        "contacts": CONTACTS["contacts"],
        "jobs": jobs,
    }


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_config(path, **overrides):
    config = json.loads(json.dumps(BASE_CONFIG, ensure_ascii=False))
    config.update(overrides)
    return write_json(Path(path), config)


def write_fixtures(root, *, jobs=None, contacts=None, browser=None):
    root = Path(root)
    write_json(root / "jobs.json", JOBS if jobs is None else jobs)
    write_json(root / "contacts.json", CONTACTS if contacts is None else contacts)
    write_json(root / "browser.json", browser_script() if browser is None else browser)
    return root


def cli_env(path_prefix=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    if path_prefix is not None:
        env["PATH"] = str(path_prefix) + os.pathsep + env.get("PATH", "")
    return env


def run_cli(cwd, data_dir, *args, path_prefix=None, timeout=180, commands=()):
    command = [sys.executable, "-m", "legwork", "--data-dir", str(data_dir), *args]
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=cli_env(path_prefix),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def decoy_browser(path_prefix):
    """放一个会在被调用时留下标记的 ego-browser，用于证明模拟路径没有操作真实浏览器。"""
    prefix = Path(path_prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    marker = prefix / "ego-browser-called"
    script = prefix / "ego-browser"
    script.write_text(
        "#!/bin/sh\n"
        f'echo called >> "{marker}"\n'
        "echo 'ego-browser should not be invoked in simulated tests' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return marker


def model_results(packet, *, statuses=None, action="send", greetings=None):
    """按 CLI 契约构造一份完整批次结果；不是从实现推导的。"""
    statuses = statuses or {}
    greetings = greetings or {}
    results = []
    for item in packet["jobs"]:
        conditions = [
            {
                "id": requirement["id"],
                "status": statuses.get((item["job_id"], requirement["id"]), statuses.get(requirement["id"], "met")),
                "evidence": "岗位描述原文：常驻示例城市办公室。",
            }
            for requirement in packet["hard_requirements"]
        ]
        result = {
            "job_id": item["job_id"],
            "action": action,
            "reason": "示例判断理由。",
            "conditions": conditions,
        }
        if action == "send":
            result["greeting"] = greetings.get(
                item["job_id"], "您好，我想应聘{title}岗位。".format(title=item["title"])
            )
            result["resume_fact_ids"] = ["rf_001"]
        results.append(result)
    return {"packet_id": packet["packet_id"], "results": results}


def write_fake_host(root):
    """生成宿主替身的可执行入口；替身本身在 tests/fake_host.py，不发真实模型请求。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    shim = root / "host-shim"
    shim.write_text(
        "#!/bin/sh\nexec {python} {script} \"$@\"\n".format(
            python=sys.executable, script=REPO / "tests" / "fake_host.py"
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim
