# 参与 Legwork

感谢你愿意花时间。这个项目对「自动化」有明确边界，改动也按同样的标准审：**先证明安全，再谈效率**。

## 快速开始

```bash
git clone https://github.com/zzf2333/Legwork.git
cd Legwork
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
python3 -m unittest discover -s tests      # 105 项
ruff check legwork tests
node tests/js/guard_contract.mjs
node tests/js/locate_contract.mjs
```

不需要真实账号就能验证大部分行为：`--fixtures` 让采集和浏览器走离线替身，`tests/fake_host.py` 替代宿主 agent。

## 提 issue 前

请先确认这几件事，能省掉一半往返：

1. **不要贴真实数据**：简历、聊天记录、招聘者姓名、会话 ID、消息 MID、账号 ID 都属于隐私。需要还原问题时用 `demo-001` 这类虚构值。
2. 带上 `legwork status` 的输出（不含 JD 与聊天内容）、复现命令、退出码，以及 `{"error": {"code": ...}}` 那段 JSON。
3. 说明是 `--fixtures` 离线通道还是真实通道。两者行为不同，混着说会导致排查方向错误。

## 什么改动容易被接受

- 修真实缺陷，附上能复现的失败用例。
- 补充契约测试（`tests/js/*.mjs`、`tests/test_*.py`）来锁住已经存在的行为。
- 文档纠错：与代码不符的描述、过期步骤、容易误解的边界。
- 支持新的平台来源：请先开 issue 说明页面结构与身份字段，`legwork/sources.py` 与 ego 脚本按平台隔离。

## 什么改动需要先讨论

- 改变发送、额度、占用、去重语义的任何改动。
- 新增自动发送、自动回复、批量重试、静默降级。
- 新增第三方运行时依赖（当前刻意只用标准库 + SQLite）。
- 修改 `uncertain` 的处理方式，或让程序自己生成招呼正文。

这些会影响「不会偷偷多发一条」这个前提，先谈清楚再写代码。

## 提交约定

- 一次提交只做一件能单独说明、单独回退的事。
- 提交信息用中文，格式：`<类型>(<范围>): <一句话>`，类型取 `feat` / `fix` / `refactor` / `docs` / `test` / `chore`；正文写清背景、改动、验证命令与结果，以及未验证项。
- 提交前必须跑通 `unittest` 与 `ruff check`。CI 会在 Python 3.11 / 3.12 / 3.13 上再跑一遍。
- 测试测真实实现，不把业务逻辑抄进测试；断言针对行为契约，不锁死实现细节。

## 审核标准

审查主要看四件事，与改动大小无关：

1. 会不会在用户不知情时多发送一条消息？
2. 失败路径是否保留现场（`uncertain`、错误码、日志），而不是静默降级？
3. 有没有把用户数据带出本机（日志、提示词、异常信息）？
4. 是否只用了当前需要的最小实现，没有为假想需求加抽象？

## 许可证

贡献的代码按 [MIT](LICENSE) 授权。
