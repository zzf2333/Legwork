## 做了什么

<!-- 一句话说明这个 PR 解决什么 -->

## 为什么

<!-- 关联 issue，或说明缺陷现象 -->

## 会不会影响发送行为

- [ ] 不影响（只读、展示、文档、测试）
- [ ] 影响发送时机 / 额度 / 去重 / 重试 —— 已在此说明理由与停止条件

## 验证

```bash
python3 -m unittest discover -s tests
ruff check legwork tests
node tests/js/guard_contract.mjs
node tests/js/locate_contract.mjs
```

<!-- 粘贴实际结果；没有跑的说清原因 -->

## 检查项

- [ ] 没有提交真实简历、聊天、账号标识或平台内部 ID
- [ ] 新行为有对应的测试，且断言针对契约而不是实现细节
- [ ] 失败路径保留现场（`uncertain`、错误码），没有静默降级
- [ ] 文档已同步（README / CHANGELOG）
