# Legwork

**We do the legwork. You make the call.**

跑腿的活它干，投不投你说了算。

[![CI](https://github.com/zzf2333/Legwork/actions/workflows/ci.yml/badge.svg)](https://github.com/zzf2333/Legwork/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](pyproject.toml)

在你自己的电脑、用你自己的账号，把找工作里最重复的那部分跑完：采集岗位 → 去重 → 额度 → 投递 → 回执核对 → 中断补发。投不投、说什么，由你和你自己已经登录的 agent 决定。

> Legwork runs on your own machine with your own logged-in browser. The program performs the deterministic steps — collect, dedupe, quota, click, verify delivery. Your existing CLI agent (Codex, Claude Code, …) makes the judgement calls and writes the message. No API keys, no cloud, no hidden auto-apply.

---

## 为什么是它

| 常见做法 | Legwork 的做法 |
| --- | --- |
| 内置模型 API、把简历和聊天传到云上 | 调用你本机已登录的 agent，凭证不出门，不设 API Key |
| 一键群发、每天几百条 | 有硬性条件、有每日额度、有去重口径；超了就不发 |
| 「发出去了」就当成功 | 必须有唯一送达回执；核对不到就记 `uncertain`，不谎报成功 |
| 断了不知道怎么办 | 中断停在 `uncertain`，补发要你显式指定岗位，不自动重试 |
| 用现成简历海投 | 招呼只能引用你写下的简历事实 ID，一条不许编 |

## 它做什么，不做什么

| 程序做（可审计的固定动作） | 你的 agent 做（判断） |
| --- | --- |
| 采集岗位、按岗位/招聘者/公司去重 | 逐岗判断硬条件 `met` / `unmet` / `unknown` 并给证据 |
| 校验配置、额度、账号与岗位身份 | 选出值得投的岗位 |
| 落盘占用、点击「立即沟通」 | 写定制招呼正文，只引用简历事实 ID |
| 核对会话身份、草稿、送达回执 | 给出 `skip` 的理由 |

程序不替你决定投不投，也不替你写话；agent 不接触你的浏览器、不自己点按钮。两边通过批次文件和 JSON 结果交接，格式由 [schema](legwork/resources/schema/model_result.schema.json) 约束。

## 工作方式

```
你写的配置：简历事实 · 硬条件 · 额度 · 城市
      │
      └─ legwork run
            采集 → 去重 → 额度/身份检查 → 建立运行
                   ⇅  程序自动调用你本机的宿主模型：判断 + 写正文
            校验决策 → 点击 → 核对送达回执
           └─ legwork today   一岗一行看结果，异常留给你决定
```

默认 `run` 会先做完本轮全部准备，再进入发送；加 `--prepare-only` 只准备不发送。

内部阶段（`prepare` / `packet` / `decide` / `finalize` / `send`）仍然存在，但由 `run` 串起来，普通使用不需要逐条调用。

## 快速开始

### 前提

- Python 3.11+，macOS 或 Linux（进程互斥用 `fcntl`）。同一 `browser.space` 的数据目录共用一个锁，放在各自数据目录的父目录下；不同方向共用一个 ego 空间时，把它们放在同一父目录即可互斥。
- [ego-browser](https://www.npmjs.com/package/ego-browser)（ego lite）：首版显式依赖它操作浏览器，不承诺通用浏览器支持。
- 你自己已登录的 BOSS 直聘账号，以及一个能执行 Bash 的 CLI agent（负责判断和写招呼）。

不需要 pip 安装，也不用单独安装 Skill。浏览器部分要你手动准备：装好 ego lite、登录、把城市选到配置里的城市、记下空间名与两个页面标签（例如 `p1`、`p2`），填进配置。首次使用前建议先跑一次 `ego-browser skill` 阅读当前版本的用法。

### 三步

先下载并在该目录工作：

```bash
git clone https://github.com/zzf2333/Legwork.git
cd Legwork
```

在这个目录打开你已登录的 CLI agent，说「跑一轮投递」。agent 按仓库自带的 Skill 执行：首次用 `legwork/resources/skills/legwork-configure/SKILL.md` 生成配置，之后每次用 `legwork/resources/skills/legwork-run/SKILL.md` 跑一轮。

主路径只有一条命令：

```bash
python3 -m legwork run --config ~/legwork/config.json
```

它完成采集 → 去重 → 额度与身份检查 → 调用本机宿主模型判断并写正文 → 校验决策 → 发送 → 核对送达回执。不用自己导出批次或手写 JSON。

配置和数据都放仓库外，避免误提交：配置建议 `~/legwork/config.json`，数据目录用 `--data-dir ~/legwork/data`。仓库默认的 `./.legwork` 已在 `.gitignore` 里，但一个数据目录只服务一个账号、一种通道。

一个账号按方向拆成多个数据目录时（例如 `~/legwork/data-frontend`、`~/legwork/data-fullstack`），把它们的配置指向同一个 `browser.space`，并放在同一父目录下：采集和发送都会先取该空间的共享锁，第二个进程直接报 `browser_busy`，不会两个进程同时操作同一组页面。

### 先离线演练一次（不接触真实平台）

```bash
cp examples/config.example.json /tmp/legwork.config.json
python3 -m legwork --data-dir /tmp/legwork-data prepare --config /tmp/legwork.config.json \
  --fixtures examples/fixtures --now 2026-03-02T10:00:00+08:00
python3 -m legwork --data-dir /tmp/legwork-data status --config /tmp/legwork.config.json
```

`--fixtures` 让输入和浏览器都走离线替身：不启动 ego-browser，不会向任何人发消息。

整条链路也能一条命令跑完。仓库里有一个只读 fixture 的宿主替身 `tests/fake_host.py`，给它做一个可执行入口（放在仓库外）：

```bash
cat > /tmp/host-offline <<EOF
#!/bin/sh
exec python3 "$PWD/tests/fake_host.py" "\$@"
EOF
chmod +x /tmp/host-offline
python3 -m legwork --data-dir /tmp/legwork-data2 run --config /tmp/legwork.config.json \
  --fixtures examples/fixtures --host-command /tmp/host-offline \
  --now 2026-03-02T10:00:00+08:00
```

示例配置里 `live=false`，所以这条命令会停在 `ready_live_disabled`，零发送——这正是预期行为。

### 首次真实运行前

以 `examples/config.example.json` 为格式参考，按自己的账号、城市、关键词、简历事实、硬条件、额度和时区填写，写到仓库外。

- 校验：`python3 -m legwork config check --config ~/legwork/config.json`（配置 Skill 会做这一步）。
- `live=false` 时只允许 `--fixtures`；确认过流程、条件和话术后再改成 `true`。
- 发送前自己核对目标、正文、数量与停止条件。

仓库只带虚构的示例配置和 fixture。简历、投递条件、账号、城市和额度由你在本机填写，不写进仓库。

## 命令

主路径：

| 命令 | 作用 |
| --- | --- |
| `run` | 跑一轮：准备 → 调用宿主判断 → 校验 → 发送。日常只用这一条 |
| `config check` | 校验配置，输出账号绑定与 `config_digest` |
| `today` | 今日投递清单一岗一行；`--job <job_id>` 看完整 JD、话术与执行结果。零模型、零浏览器 |
| `followup` | 对 `uncertain` 岗位向**已存在会话**补发定制正文；不点「立即沟通」、不新增占用，必须 `--job` 指定 |
| `status` | 状态计数、今日额度与最近运行，不含 JD、简历或聊天内容 |
| `assets` | 打印随包资源路径（schema、JS、Skills） |

分步命令（排障与离线演练用，不是普通用户路径；`run` 内部就是按这个顺序调用）：

| 命令 | 作用 |
| --- | --- |
| `prepare` | 采集条目：入选的待判断、排除的写明原因、无稳定 ID 的记采集异常；建立运行 |
| `packet` | 导出一批岗位、简历事实与语义条件；空批次输出 `packet_id: null` |
| `decide` | 整批校验并原子接收 agent 结果；重复提交相同结果幂等，已执行项不改写 |
| `finalize` | 核对准备完成：只有候选已处理完或预算耗尽才允许结束 |
| `send` | 只发送准备完成且仍有效的记录；核对账号、岗位、JD 摘要、历史、草稿与回复 |

分开调用时：`prepare` → 把 `packet` 导出的批次交给 agent 判断 → `decide` 接收结果 → `finalize` 核对 → `send`。只有本轮候选已处理完或预算耗尽才能完成准备；`failed` 必须用 `run --resume-run <run_id>` 显式续作。

退出码：`0` 成功；`2` 配置或用法错误；`3` 按配置/状态拒绝；`4` 浏览器或宿主失败、或发送结果不确定。拒绝与错误以 JSON 写到 stderr：

```json
{"error": {"code": "hard_requirement_unmet", "message": "岗位 job-a1 的硬条件 hr_office 判定为 unmet，不能发送"}}
```

## 配置

配置是一份 JSON，除 `collect_per_keyword` 外字段全部必填，程序不内置城市、关键词、额度或话术：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 固定 `1` |
| `account_id` | 本地账号标签，一个数据目录只绑定一个账号 |
| `platform_user_id` | 你自己账号的平台数字 ID，发送前用它核对登录身份 |
| `city_code` / `city_name` | 平台城市编码与名称；程序只用你已选好城市的那个页面，不自动切换 |
| `keywords` | 搜索词表 |
| `resume_facts` | `[{"id","text"}]`，可溯源的简历事实；招呼只能引用这些 ID |
| `hard_requirements` | 硬条件数组，见下 |
| `preferences` | 偏好备注，原样交给 agent |
| `dedupe_scope` | `job` / `boss` / `company` 的组合，跨任务与批内去重口径 |
| `daily_limit` | 每日首次沟通额度，按已占用计数并跨运行累计 |
| `batch_size` | 每批岗位数，1..20 |
| `collect_per_keyword` | 每个关键词停止继续加载的阈值，0..100；缺省 `0` 只读首屏，大于 0 时滚动加载更多；按整页读取，实际条数可能超过该值 |
| `browser` | `{"space","job_page","chat_page"}`，Ego 空间名或编号加两个页面标签 |
| `timezone` | 判定「今天」和旧任务的时区 |
| `live` | 是否允许操作真实平台；`false` 时只允许 `--fixtures` |

硬条件分三类。程序条件只看 `title` / `description` / `address` 的包含或排除，处理能确定的字段：

```json
{"id": "hr_city", "kind": "program", "field": "address", "op": "contains", "value": "示例城市", "text": "工作地点在示例城市"}
```

语义条件交给 agent 逐项回答并给证据；`unknown_policy=skip` 表示信息不明就不投，`allow` 表示允许投：

```json
{"id": "hr_shift", "kind": "semantic", "text": "不需要长期夜班", "unknown_policy": "skip"}
```

`unmet`，或 `skip` 策略下的 `unknown`，都不会被放行发送。

第三类只看采集到的招聘方活跃时间，只写 `id` 和 `text`：

```json
{"id": "hr_active", "kind": "activity", "text": "招聘方最近一周内活跃"}
```

加上这一条后，活跃时间为「一周外」（`2周内活跃`、`本月活跃`、`3月内活跃`、`半年前活跃`、`近半年活跃`）的岗位在准备期直接排除，原因 `inactive_recruiter`，不占额度、不调用模型。活跃文本读不到时**不排除**：读不到不等于久未活跃。`activity` 条件不支持 `field`/`op`/`value`，写了会被配置校验拒绝，避免被误当成关键词匹配。

## 安全与边界

这些不是可调参数，是代码里的前提：

- **额度**：每日首次沟通数按已占用计数、跨运行累计，到上限立即停止。
- **占用先落盘**：点击前先写 `attempts`（账号 + 岗位唯一），进程崩了也不会重复点。
- **回执才算成功**：要求消息 MID 与会话 ID 都存在且唯一；核对不到就是 `uncertain`，不写 `sent`。
- **默认招呼不可撤回**：「立即沟通」会先发出平台默认问候。之后若中断，只能用 `followup --job <job_id>` 向同一会话补发定制正文，无法撤销那条默认问候。
- **遇到异常就停**：结果不明、账号不符、历史不完整、岗位或 JD 变化、对方已回复、验证码、失去控制——本轮立即停止，剩余岗位保持 `ready`，不做刷新重试、不切空间、不手工补点。
- **不读历史聊天**：只把「是否已联系过」当作去重信号，不把聊天正文当作内容来源。

## 宿主要求

`run` 只做一件事：把你本机的宿主命令当作「判断引擎」调用一次。

```bash
<host-command> exec --sandbox read-only --output-schema <包内 schema> \
  --output-last-message <结果文件> --json <提示词>
```

- 默认 `--host-command codex`，可换成本机任何**兼容这套参数**的命令；换成不兼容的 CLI 需要自己写适配层。
- 复用宿主自己的登录与权限，不复制凭证、不设置 API Key、不读取认证文件。
- 不加 `--skip-git-repo-check`、`--dangerously-bypass-approvals-and-sandbox` 之类跳过信任或自动批准的参数，因此工作目录需要在受信任的 git 仓库里。
- 登录过期、休眠、订阅额度用尽都会让本轮失败；失败时本轮不发送，也不自动重启模型调用。
- 能取到的 usage 原样写进 `<run_dir>/usage.json`，取不到写 `null`，不估算。

宿主输出质量不在程序保证范围内：程序只校验格式、预算与契约。

## 数据与状态

`<data-dir>/state.sqlite3` 保留运行、岗位快照、同日来源、采集异常、批次、决策、发送占用与平台联系历史。一个数据目录对应一个账号，换账号就换目录。

- 状态 `awaiting_decisions` / `ready` / `sending` / `uncertain` / `sent` / `skipped` / `excluded` / `stale` 由岗位状态投影得出，不重复存可推导状态。
- 快照字段是 `job_id` / `company` / `boss_id` / `boss_name` / `title` / `salary` / `address` / `description` 八项。招聘方活跃时间（`boss_active`，如 `刚刚活跃`）与公司规模（`company_size`，如 `100-499人`）也照常采集，但每天在变，所以不进快照摘要、不影响同日复用与发送前 JD 比对，存在 `job_meta` 侧表；这两项只用于准备期排除、取批排序和模型的判断输入。平台不提供发布时间，取批再带上本账号首次采集日期（`first_collected_day`，取 `job_sources` 里该岗位的 `MIN(day)`）：它是本工具的本地事实，同样不进快照摘要、不影响同日复用与发送前 JD 比对，只用于取批排序和模型输入。
- 取批顺序：先按首次采集日期，越新越靠前（没有采集记录的岗位排在有记录之后），再按公司规模 `中小` → `中大型` → `大型` → 未知，同规模内按招聘方 `一周内` → 未知 → `一周外`，最后按岗位 ID。同一天同一批候选的顺序因此稳定，额度不够时先取新出现、中小公司、最近活跃的岗位。
- 状态库 schema 目前是 `5`；旧版本数据目录在写表前直接拒绝，不自动迁移也不清空。`job_meta` 侧表用 `CREATE TABLE IF NOT EXISTS` 补建，不改 schema 版本，旧数据目录打开后仍可用（缺行读作空字符串）。升级到带 `job_meta` 的版本后，升级前导出的未消费批次会因为 `packet_document` 新增 `boss_active`/`company_size` 而改变 `input_digest`，`decide` 时报 `packet_input_mismatch`；重新 `packet` 导出新批次即可，不要手工改旧文件。带 `first_collected_day` 的批次同理，也只需要重新 `packet`。
- 同日同配置、同一份 JD 已接收的结果会复用，不重复调用模型；JD 或配置变化的未发送结果标为 `stale`，原正文仍可查。
- 同日同配置且 JD 未变的 `skip` 会复用；跨日可能重新判断，要长期排除请改条件或关键词。
- `browser.lock` 只防本机两个进程同时操作浏览器，不是平台分布式锁。

## 浏览器操作面

`legwork/ego.py` 与 `legwork/resources/js/` 是唯一的平台操作面：

| 脚本 | 职责 |
| --- | --- |
| `ego_collect.js` | 在已登录、城市已选好的列表页按卡片读岗位；身份或账号不符就停 |
| `ego_search.js` | `search` 动作的脚本体：按关键词搜索，按 `collect_per_keyword` 滚动加载更多，到阈值或列表标记 `hasMore` 为 false 就停；每步读整页，结果可能超过配置条数 |
| `ego_page_state.js` | 读详情页与聊天页的规范化状态（账号、岗位身份、历史是否完整、草稿、送达） |
| `ego_click_guard.js` | 点击前重新核对会话身份、历史完整性、草稿与是否已有回复 |
| `ego_chat_locate.js` | 会话列表是虚拟滚动、只渲染可视窗口，所以从头递减滚动查找；同一身份出现两次就停，不猜 |

发送顺序：定位并打开目标岗位 → 核对账号、岗位身份与 JD 摘要 → 核对历史完整且无既有联系 → 点「立即沟通」→ 按身份滚动查找并选中会话 → 核对唯一的己方默认消息已送达 → 填定制正文 → 再核对会话未变 → 发送 → 等待并核对定制正文的唯一送达回执。

历史加载最多推进 30 批，遇到无进展或超时停止，只有页面明确显示完整历史才继续。搜索时临时监听网页正常产生的列表请求完成，不主动重放接口。配置 `collect_per_keyword` 后，搜索完首屏再滚动列表底部，每轮至少要多出一条岗位，步数不超过该阈值；每步读整页，结果可能超过配置条数。单个关键词加载更多失败或超时只记采集异常（`load_more_failed`），保留已加载条目并继续下一个关键词。

## Skills

`legwork/resources/skills/` 下有两份 Skill，随仓库提供，不用单独安装：

- `legwork-configure`：第一次使用时把需求整理成配置，只摘取可溯源事实。
- `legwork-run`：跑一轮，含停止条件。

仓库根目录的 `AGENTS.md` 指向这两份 Skill，Codex、Claude Code、Pi 等入口打开仓库就能读到。宿主不读 `AGENTS.md` 时，直接按文件路径读，或按它自己的发现方式复制/软链。Skill 不依赖某个宿主的工具名，`python3 -m legwork assets` 可打印实际路径。

## 定时

示例见 [`examples/scheduler/crontab.example`](examples/scheduler/crontab.example) 与 [`examples/scheduler/com.example.legwork.run.plist`](examples/scheduler/com.example.legwork.run.plist)。工具只提供示例，不会自动安装、加载或卸载任何服务。

定时同样只有一条命令：工作目录设为克隆下来的 Legwork 目录（`run` 的宿主调用要求 cwd 在受信任的 git 仓库里），用 `python3 -m legwork ... run` 调用，不需要 pip 安装。

真实通道（不带 `--fixtures`）不接受 `--now`：发送日期按配置时区的实际时间判定，发送前与每次占用前都会重新取时间，跨日立即停止。`--now` 只用于离线复查。

## 常见问题

**会不会替我海投？**
不会。每日额度、硬性条件、去重口径和「必须先准备完成」这几道闸都在代码里；不足额度的岗位留作待判断并写明原因，不算不匹配。

**和平台官方有关吗？**
没有。名字不带平台商标，不使用任何私有接口，只操作你自己已登录的浏览器页面。

**换模型要改代码吗？**
不用。判断由你本机的宿主 CLI 完成，`--host-command` 指向哪个就是哪个；程序不关心它背后是哪家模型。

**发出去的消息我不满意怎么办？**
正文由你（和你的 agent）决定、发送前可核对。已经送达的消息平台侧无法撤回，这也是为什么默认流程要求发送前确认。

## 已知限制

- 只在作者本人账号、单城市、少量关键词上验证过。站点改版、账号状态、页面标签或登录态变化都可能让本轮失败，失败即停止，不猜测重试。
- 只实现了 BOSS 直聘一个平台（`sources` 层按平台隔离）。名字不承诺其他平台已支持。
- 只做首次沟通与定制招呼：不发送附件简历、不自动回复招聘者、不做多用户托管。
- 采集默认只读每个关键词首屏；设置 `collect_per_keyword` 后滚动加载到该阈值或列表标记 `hasMore` 为 false。这是停止继续加载的阈值，按整页读取，实际条数可能超过配置值；仍不承诺遍历站点全部搜索结果。发送时目标不在列表就停止，不猜相似岗位。
- 不加结果缓存：已被 `skip` 的岗位下一轮仍会重新判断（低 Token 靠「一次一个批次、只带本批岗位与事实」实现）。
- 旧 schema（含开发版 3、4）不自动迁移，请用新的隔离数据目录，原数据保留供核对。

## 路线

- **v0.0.x（当前）**：手动 / 半自动投递。准备段可一条命令跑完，发送段需要显式触发。
- **v0.1**：把「准备完成 → 发送」做成可配置的自动流程（需要先用稳妥岗位定义好停止条件），并加入招聘者回复的跟进处理。
- 更远：多平台来源、面试安排、投递复盘。每一项都要先证明「不刷屏、可回滚」，才进入实现。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v     # 151 项
ruff check legwork tests                      # 通过
node tests/js/guard_contract.mjs              # 点击守卫契约
node tests/js/locate_contract.mjs             # 虚拟滚动定位契约
for f in legwork/resources/js/*.js; do node --check "$f"; done
python3 -m legwork assets                     # 随包资源路径
```

CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在 Python 3.11 / 3.12 / 3.13 上跑同一组命令。仓库没有类型检查配置，因此不宣称类型检查通过。

离线链路不需要真实账号：`--fixtures` 加 `tests/fake_host.py` 就能跑完 prepare → 判断 → 校验 → 发送前停止。

## 参与

见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按 [SECURITY.md](SECURITY.md) 反馈，**不要在 issue 里贴简历、账号、聊天截图或平台内部 ID**。

## 许可证

[MIT](LICENSE)。
