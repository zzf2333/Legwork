# Legwork 仓库说明

## 用户入口（唯一主路径）

用户下载本仓库后，在这个目录打开自己已登录的 CLI agent，说「跑一轮投递」。

```bash
git clone https://github.com/zzf2333/Legwork.git
cd Legwork
python3 -m legwork run --config ~/legwork/config.json
```

- 首次使用：按 `legwork/resources/skills/legwork-configure/SKILL.md` 帮用户生成配置。
- 之后每次：按 `legwork/resources/skills/legwork-run/SKILL.md` 跑一轮，含停止条件。
- 不需要 pip 安装，也不需要另外安装 Skill。不要引导用户改走 `prepare` / `packet` / `decide` / `finalize` / `send`；那是排障与离线演练用的分步入口。
- 用户配置放仓库外（例如 `~/legwork/config.json`），运行数据用 `--data-dir`。一个数据目录只服务一个账号、一种通道。
- 遇到结果不明、账号不符、验证码、页面结构变化、对方已回复：停下报告现状，不刷新重试、不切空间、不手工补点。补发只用 `legwork followup --job <job_id>`。

## 开发约定

- 用户文档在 `README.md`，已发布状态在 `CHANGELOG.md`；改行为时同步这两处和受影响的 Skill（`legwork/resources/skills/`）。
- 验证命令见 README「开发与验证」：`python3 -m unittest discover -s tests -v`、`ruff check legwork tests`、`node tests/js/guard_contract.mjs`、`node tests/js/locate_contract.mjs`。
- 平台操作面只在 `legwork/ego.py` 与 `legwork/resources/js/`。新增读取或点击动作改这里，不要在别处操作浏览器。
- 仓库里只放虚构示例。真实简历、账号、聊天记录、平台内部 ID 不进仓库，也不进提交信息和 issue。
