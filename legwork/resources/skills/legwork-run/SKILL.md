---
name: legwork-run
description: 手动或定时执行一轮 BOSS 投递：准备候选、导出批次、调用宿主模型做语义匹配、校验决策、发送首次沟通。用户说「跑一轮投递」「看看今天有哪些岗位」「继续上次没跑完的批次」时读取本 Skill。
---

# 跑一轮投递

程序负责采集、去重、额度、占用和点击；agent 只负责判断语义匹配与写招呼正文。不要用关键词规则替代语义判断，也不要跳过校验直接点发送。

## 运行时边界

配置、简历和状态数据都放在用户本机的仓库外目录（例如 `~/legwork/config.json`、`--data-dir ~/legwork/data`），不要写进源码、默认配置或仓库示例。仓库里的岗位、公司、账号都是虚构数据；真实账号、城市、经历只在用户自己的实例里填写。

## 手动一轮

以下简写省略数据目录；实际调用时，所有命令都使用同一用户实例的 `--data-dir <目录>`，配置路径也使用该实例的文件。

```bash
legwork prepare --config legwork.config.json          # 真实平台，需 live=true
legwork packet  --config legwork.config.json --run-id <run_id>
legwork decide  --config legwork.config.json --run-id <run_id> --packet <批次文件> --results <结果文件>
legwork finalize --config legwork.config.json --run-id <run_id>
legwork today   --config legwork.config.json           # 查看今日清单，不调模型
legwork send    --config legwork.config.json --run-id <run_id>
```

准备与发送是两个阶段：`decide` 只接收模型结论并保存正文；`send` 只消费已保存且仍有效的记录。只有本轮候选已处理完或预算耗尽才能完成准备；`decide` 会自动检查，也可用 `finalize` 核对条件。未完成任务会以 `prepare_incomplete` 拒绝；`failed` 必须通过 `run --resume-run` 显式续作，不能强行finalize。没有额度安排判断的岗位保留为待判断并写明原因。

`prepare` 的真实通道会先滚动加载完整联系历史（最多30批，无进展即停止），再搜索和读取岗位；这些步骤由脚本完成，不需要 agent 逐页操作。只查看岗位时运行到 `prepare` 或 `packet` 即停止，不调用 `send`/`run`。

1. `prepare` 输出 `run_id`、入选数量和被拒原因（`program_requirement`、`platform_history`、`duplicate_in_batch`、`prior_run`、`missing_detail`、`not_first_contact`、`inactive_recruiter` 等），以及采集异常数量（无稳定 ID 的条目）。被拒岗位不要人工绕过。
2. `packet` 写出批次文件（`{"packet_id": null, "jobs": []}` 表示本轮没有预算内待处理候选，此时不要调用模型）。取批顺序固定：先中小公司，再中大型、大型、未知规模；同规模内先最近活跃（`一周内`）的岗位，最后按岗位 ID 稳定排序。额度不够时，没进本批的岗位保留为待判断，不要自己改成选中哪几个。
3. 把批次文件交给宿主 agent：逐岗判断 `hard_requirements` 中每个语义条件，输出符合 `resources/schema/model_result.schema.json` 的 JSON。批次里每个岗位带 `boss_active`（如 `刚刚活跃`）与 `company_size`（如 `100-499人`），可直接写进判断理由和招呼；平台没给时是空字符串，不要补编。
4. `legwork decide --config ... --run-id ... --packet <批次文件> --results <结果文件>`。整批一次校验：缺少岗位、多出岗位、条件缺失、`unmet` 或 `skip` 策略下的 `unknown`、引用不存在的简历事实都会被拒绝，且一次拒绝不落盘任何岗位。
5. `legwork finalize` 核对准备已完成或预算已耗尽，不能跳过剩余判断；`today --job <job_id>` 可逐岗核对完整 JD、话术与状态。之后 `legwork send --config ... --run-id ...`。只有 `live=true` 才实发；发送前会核对账号、岗位身份、JD 摘要与平台历史，点击后核对目标会话、默认消息、草稿与送达回执（消息 MID + 会话 ID）。中断或结果不明会保留 `uncertain`，再次执行不会重发同一岗位。
6. 遇到结果不明、账号不符、历史不完整、岗位或 JD 变化、对方已回复：本轮立即停止，剩余岗位保持 `ready`。先人工核对平台现状，再决定是否续作。

## 定时一轮（Codex 入口）

```bash
legwork run --config /绝对路径/legwork.config.json
```

- 只调用 `codex exec --sandbox read-only`，复用用户自己的登录与权限；不复制凭证、不设 API Key、不添加自动批准或跳过信任检查的参数。
- 先完成本轮全部准备批次，再进入发送；发送阶段不调用模型、不重写正文。加 `--prepare-only` 只准备不发送。
- 准备预算 = 当日实际剩余额度 − 本轮未占用的有效 `ready`；手动和自动入口使用相同限制。预算缩减时重新导出较小批次，旧批次及迟到结果不可再用，不手工修改旧文件。未安排的候选保留为待判断并写明原因。
- 批次为空时不启动模型；全部岗位 `skip` 属于正常结束。
- 真实通道不接受 `--now`：发送日期按配置时区的实际时间判定，跨日停止。`--now` 只用于离线演练。
- 宿主失败时退出码 4，本轮不发送；`run_id` 会打印出来，用 `--resume-run <run_id>` 继续同一任务，不要每次都新建计划。
- 需要 `codex` 在受信任的 git 仓库目录下运行；工具不会添加 `--skip-git-repo-check`。

## 离线演练

```bash
legwork prepare --config legwork.config.json --fixtures examples/fixtures --now 2026-03-02T10:00:00+08:00
legwork run     --config legwork.config.json --fixtures examples/fixtures
```

`--fixtures` 使用离线 fixture 和模拟浏览器，不会启动 ego-browser，也不会向任何人发消息。离线演练只证明流程与校验，不证明真实平台可用。

同一个数据目录只服务一种通道：fixture 运行不能被真实通道接手，真实运行也不能用 fixture 通道续作。两种都跑就准备两个 `--data-dir`。

## 停止条件

验证码、失去页面控制、账号变化、页面结构变化、联系历史不完整、对方已回复、结果不明：停下来报告现状，不要刷新重试、不要切换空间、不要手工补点发送。
