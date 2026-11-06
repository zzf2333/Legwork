"""测试用宿主替身：按 CLI 契约生成结果，不进行任何真实模型调用。

CLI 以 `codex exec --sandbox read-only --output-schema ... --output-last-message ...`
调用宿主；本替身只读取同一份 packet 并写出契约形状的结果，便于离线验证整条链路。
环境变量：
- FAKE_HOST_ARGV：记录收到的实参，供测试断言参数安全边界。
- FAKE_CODEX_FAIL：非空时模拟宿主失败。
- FAKE_CODEX_MODE：send（默认）/ skip / partial / unmet。
"""
import json
import os
import re
import sys

USAGE = {"type": "turn.completed", "usage": {"input_tokens": 1200, "output_tokens": 300}}


def main():
    argv = sys.argv[1:]
    log_path = os.environ.get("FAKE_HOST_ARGV")
    if log_path:
        with open(log_path, "w", encoding="utf-8") as handle:
            json.dump(argv, handle, ensure_ascii=False)

    if os.environ.get("FAKE_CODEX_FAIL"):
        sys.stderr.write("simulated host failure\n")
        return 3

    out_path = None
    for index, item in enumerate(argv):
        if item in ("-o", "--output-last-message"):
            out_path = argv[index + 1]
    if not out_path:
        sys.stderr.write("missing --output-last-message\n")
        return 9

    joined = " ".join(argv)
    match = re.search(r"(/[^\s\"']+packet[^\s\"']*\.json)", joined)
    if not match:
        sys.stderr.write("packet path not found in prompt\n")
        return 9
    with open(match.group(1), encoding="utf-8") as handle:
        packet = json.load(handle)

    mode = os.environ.get("FAKE_CODEX_MODE", "send")
    results = []
    for index, item in enumerate(packet["jobs"]):
        conditions = [
            {"id": requirement["id"], "status": "met", "evidence": "岗位描述原文：常驻示例城市办公室。"}
            for requirement in packet["hard_requirements"]
        ]
        action = "skip" if mode == "skip" else "send"
        result = {
            "job_id": item["job_id"],
            "action": action,
            "reason": "替身模型的示例判断。",
            "conditions": conditions,
        }
        if mode == "unmet" and index == 0:
            conditions[-1]["status"] = "unmet"
        if action == "send":
            result["greeting"] = "您好，我想应聘{title}岗位。".format(title=item["title"])
            result["resume_fact_ids"] = [packet["resume_facts"][0]["id"]]
        else:
            result["greeting"] = ""
            result["resume_fact_ids"] = []
        results.append(result)
    if mode == "partial":
        results = results[:-1]

    payload = {"packet_id": packet["packet_id"], "results": results}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    sys.stdout.write(json.dumps(USAGE, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
