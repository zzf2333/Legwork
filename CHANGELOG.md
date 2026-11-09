# 更新记录

## 未发布

- 发送前的 JD 比对不再包含地址：直接打开岗位详情页时 `.location-address` 与 `window._jobInfo.address` 都取不到，写入摘要会让每次发送都判 `snapshot_changed` 而停轮。采集和「地址含成都」规则仍用地址，同日复用与占用比对的摘要也不变。
- 会话身份改为 job + boss（另加 uid、会话 ID），不再要求公司名相同：猎头代招岗位在会话列表显示的是猎头方公司名，与岗位详情页不同，旧比对会导致点完「立即沟通」后定位不到会话、超时记 `uncertain`。岗位页一侧仍比对公司名。

## 0.0.1 — 首个公开预览版

首次以 v0.0.1 公开。以下内容为公开发布时点的实际状态，未包含未验证的承诺。

### 命名

- 项目名 **Legwork**（“We do the legwork. You make the call.”）：跑腿的活由程序干，判断由你和你自己的 agent 做。
- PyPI 包名 `legwork-cli`（`legwork` 已被占用），CLI 命令 `legwork`，Python 模块 `legwork`。
- 早期内部名 `boss-assistant` / `boss_assistant` 已全部替换；旧名没有对外发布过，不提供兼容别名。
- 名字不含平台商标，与 BOSS 直聘官方无关；目前仅实现该平台一个来源。

### 能力

- **CLI 全流程**：`config check` / `prepare` / `packet` / `decide` / `finalize` / `send` / `followup` / `today` / `status` / `run` / `assets`。
- **固定动作在程序里**：采集、按岗位/招聘者/公司去重、每日额度、发送前落盘占用、点击「立即沟通」、送达回执核对、状态回写。语义匹配与招呼正文由宿主 agent 判断，程序只校验格式与契约。
- **宿主无关的模型调用**：`run` 通过 `--host-command` 调用你本机已登录的宿主 CLI（默认 `codex`，需兼容 `codex exec --output-schema` 接口），不内置 API Key、不复制凭证、不绑定模型厂商。
- **中断可恢复**：点击后结果不明记 `uncertain`，不自动重发；用 `followup --job <job_id>` 向已有会话补发定制正文，复用原占用、不重复点击、不占新额度。
- **离线可跑**：`--fixtures` + `examples/fixtures` + `tests/fake_host.py` 可在不接触真实平台的条件下跑完整链路。
- **决策复用**：同日同配置且 JD 未变的判断结果会复用，不重复调用模型；JD 或配置变化则标为 `stale`。

### 依赖与边界

- 依赖 [ego-browser](https://www.npmjs.com/package/ego-browser)（ego lite）操作浏览器；不承诺通用浏览器或 Playwright 支持。
- 仅 macOS / Linux；进程互斥使用 `fcntl`。Python 3.11+，运行时无第三方 Python 依赖。
- 状态库 schema 为 `5`；旧 schema 目录直接拒绝，不自动迁移、不清空。
- 「立即沟通」会先发出平台默认招呼且不可撤回；本工具只能之后补发定制正文。
- 不发送附件简历、不处理招聘者回复、不做多用户托管、不承诺遍历站点全部搜索结果。

### 验证范围

- 105 项自动化测试（含 4 个浏览器脚本契约与虚拟滚动定位用例）、`ruff check` 通过。
- EGO 登录态复用、搜索、详情读取、历史加载、采集、首次点击发送与补发：在作者本人账号的真实页面上验证过；不代表其他账号、城市或站点版本。
- 真实宿主模型判断：执行过真实 `codex exec` 调用并核对单岗内容质量；更大规模的输出质量未做统计性验证。
- 尚未验证：`run` 的「准备完成即自动发送」一次性跑通（发送段已由 `run` 自动衔接，但这个组合没有在真实账号上做完整验收）、多账号、Windows、非成都城市。

### 仓库与文档

- 公开仓库只放用户文档：`README.md`、`CHANGELOG.md`、`CONTRIBUTING.md`、`SECURITY.md`。
- 内部设计与验收记录（模块契约、验收条目、真实平台证据）不随仓库发布，单独保存在本地。
- 首个公开提交为单一初始提交，历史中不含内部文档与真实投递记录。

### 许可证

MIT。
