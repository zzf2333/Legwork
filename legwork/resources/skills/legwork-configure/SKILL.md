---
name: legwork-configure
description: 与用户一起把 BOSS 投递需求整理成 legwork 的配置文件。用户说「配置投递」「设置城市/关键词/额度」「整理简历事实」或首次使用本工具时读取本 Skill。
---

# 配置 BOSS 投递

目标：把用户的口头需求整理成一份可校验的 JSON 配置。程序只执行固定动作，配置口径必须由用户明确给出，不能沿用作者的账号、城市、额度或话术。

## 步骤

1. 读取用户自己的简历文件（路径由用户提供）。只摘取可溯源的事实，每条一个稳定 ID 和一句文字，不推断、不美化、不合并不同经历。
2. 和用户确认下面每一项，缺一项就问一项，不要替用户决定：
   - 账号：本地账号标签，以及用户自己 BOSS 账号的平台数字 ID（在个人页地址或页面状态里可见）。
   - 城市：城市名与平台城市编码。工具只使用用户已登录的那一个浏览器空间和页面，不会自动切换城市。
   - 关键词：用于搜索岗位的词表。
   - 硬条件：能由程序判断的字段（标题、地址、描述里的包含/排除词）与需要语义判断的条件分开写。
   - 每个语义硬条件的 `unknown_policy`：`skip` 表示信息不明就不投，`allow` 表示信息不明也允许投。
   - 偏好、去重口径（岗位/招聘者/公司）、每日额度、批大小、时区。
   - `live`：是否允许操作真实平台。首次配置建议 `false`，先用离线 fixture 走通流程。
3. 把配置写到用户指定路径，例如 `~/legwork/config.json`（不要提交到公开仓库，也不要写进源码或默认配置）。
4. 运行 `legwork config check --config <路径>`，确认没有报错，并把 `account_id`、`config_digest` 和 `live` 报告给用户。
5. 选定专用数据目录（`--data-dir`，例如 `~/legwork/data`）：一个目录只服务一个账号和一种通道，演练用 `--fixtures`、真实运行都各自独立，不要共用同一个目录。当前 schema 为 `4`，旧版本目录会被拒绝而不是迁移或清空。
6. 浏览器准备（只在用户同意操作真实平台后）：让用户自己安装 ego lite、登录 BOSS、把城市选到配置的城市，记下空间名与页面标签（形如 `p1`、`p2`）填进 `browser`。

## 硬条件写法

- 程序条件：`{"id": "hr_city", "kind": "program", "field": "address", "op": "contains", "value": "示例城市", "text": "工作地点在示例城市"}`。`field` 支持 `title`、`description`、`address`；`op` 支持 `contains`、`excludes`。
- 语义条件：`{"id": "hr_shift", "kind": "semantic", "text": "不需要长期夜班", "unknown_policy": "skip"}`。匹配时由 agent 逐项给出 `met`/`unmet`/`unknown` 与证据。
- 不要把可以语义判断的条件写成关键词包含，那会把不确定的 JD 伪装成确定结论。

## 宿主输出形状

宿主结果必须符合随包 `resources/schema/model_result.schema.json`，所有属性都在 `required` 里：每岗都有 `job_id`、`action`、`reason`、`conditions`、`greeting`、`resume_fact_ids`。`action=skip` 时 `greeting` 用空字符串、`resume_fact_ids` 用空数组；`action=send` 时两者都不能为空，且只能引用 `resume_facts` 里已有的 ID。

## 结束条件

配置通过校验后再交给用户确认一次「城市、关键词、硬条件、额度、live」。用户的确认是唯一依据；示例配置只是格式示范，不构成投递授权。
