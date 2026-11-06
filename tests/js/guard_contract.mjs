// 在独立 Node 上下文里执行随包守卫脚本，验证读取函数在页面作用域内重建、身份切换会被拒绝。
// 由 tests/test_browser_js.py 以 `node tests/js/guard_contract.mjs` 调用；不接触真实浏览器。
import { readFileSync } from "node:fs";
import vm from "node:vm";

const guardSource = readFileSync(
  new URL("../../legwork/resources/js/ego_click_guard.js", import.meta.url),
  "utf8"
);

const SELF = "100000001";
const IDENTITY = { job_id: "job-a1", company: "示例一号科技", boss_id: "boss-01", uid: "uid-A", conversation_id: "conv-A" };

function pageFor(state) {
  const editor = {
    isContentEditable: true,
    focus() {
      context.document.activeElement = editor;
    },
    getClientRects: () => [1],
  };
  const button = {
    disabled: false,
    getClientRects: () => [1],
    getAttribute: () => null,
    classList: { contains: () => false },
    click() {
      context.clicked = true;
    },
  };
  const context = {
    activeElement: null,
    clicked: false,
    inserted: null,
    document: {
      querySelector(selector) {
        if (selector === "#chat-input") return editor;
        if (selector === ".job-detail-box .op-btn-chat") return button;
        if (selector === ".chat-conversation .btn-send") return button;
        return null;
      },
      execCommand(_name, _ui, text) {
        context.inserted = text;
        return true;
      },
    },
    Error,
    String,
  };
  context.document.activeElement = null;
  return { context, state };
}

function run(state, args) {
  const { context } = pageFor(state);
  const sandbox = { document: context.document, location: {pathname:'/web/geek/jobs'}, Error, String };
  const guard = vm.runInNewContext("(" + guardSource + ")", sandbox);
  // 真实读取函数从页面读取 DOM；这里把同一份状态挂到页面全局，仍要求守卫在页面作用域内重建它。
  sandbox.__state = state;
  const reader =
    "({ self, kind }) => (kind === 'card' ? globalThis.__state.card : globalThis.__state.chat)";
  const result = guard({ ...args, self: SELF, reader });
  return { result, context };
}

function chatState(overrides = {}) {
  return {
    platform_user_id: SELF,
    history_complete: true,
    editor_present: true,
    messages_loaded: true,
    draft: "",
    editor_id: IDENTITY.conversation_id,
    selected: { ...IDENTITY },
    messages: [
      { mid: "m1", sender: SELF, recipient: IDENTITY.uid, conversation_id: IDENTITY.conversation_id, text: "您好", delivered: true, kind: "text" },
    ],
    ...overrides,
  };
}

const card = {
  job_id: IDENTITY.job_id,
  company: IDENTITY.company,
  boss_id: IDENTITY.boss_id,
  can_add_friend: true,
};

const failures = [];

function expect(name, fn, expected) {
  try {
    fn();
    failures.push(name + ": 没有按预期拒绝");
  } catch (error) {
    if (!String(error.message).includes(expected)) {
      failures.push(name + ": 期望包含 " + expected + "，实际 " + error.message);
    }
  }
}

// 1. 读取函数在页面作用域内重建后可用：fill 成功并写入草稿。
{
  const { result, context } = run({ card, chat: chatState() }, {
    action: "fill",
    text: "您好，我想应聘该岗位。",
    identity: IDENTITY,
  });
  if (result?.filled !== true) failures.push("fill: 未确认填充");
  if (context.inserted !== "您好，我想应聘该岗位。") failures.push("fill: 未写入目标正文");
}

// 2. 会话在填写前被切换：必须按既定身份拒绝，不能采纳当前选中身份。
expect(
  "fill-identity-switch",
  () =>
    run({ card, chat: chatState({ selected: { ...IDENTITY, uid: "uid-B", conversation_id: "conv-B" } }) }, {
      action: "fill",
      text: "您好",
      identity: IDENTITY,
    }),
  "Recipient changed"
);

// 3. 会话 ID 相同但 uid 不同也要拒绝。
expect(
  "fill-uid-switch",
  () =>
    run({ card, chat: chatState({ selected: { ...IDENTITY, uid: "uid-B" } }) }, {
      action: "fill",
      text: "您好",
      identity: IDENTITY,
    }),
  "Recipient changed"
);

// 4. 目标会话里出现对方回复：停止发送。
expect(
  "fill-reply",
  () =>
    run({ card, chat: chatState({ messages: [
      { mid: "m1", sender: SELF, recipient: IDENTITY.uid, conversation_id: IDENTITY.conversation_id, text: "您好", delivered: true, kind: "text" },
      { mid: "m2", sender: IDENTITY.uid, recipient: SELF, conversation_id: IDENTITY.conversation_id, text: "可以聊聊", delivered: true, kind: "text" },
    ] }) }, {
      action: "fill",
      text: "您好",
      identity: IDENTITY,
    }),
  "Incoming reply"
);

// 5. 其它会话的消息不阻塞目标岗位。
{
  const { result } = run({ card, chat: chatState({ messages: [
    { mid: "m1", sender: SELF, recipient: IDENTITY.uid, conversation_id: IDENTITY.conversation_id, text: "您好", delivered: true, kind: "text" },
    { mid: "m9", sender: "uid-other", recipient: SELF, conversation_id: "conv-other", text: "别的会话", delivered: true, kind: "text" },
  ] }) }, {
    action: "fill",
    text: "您好，我想应聘该岗位。",
    identity: IDENTITY,
  });
  if (result?.filled !== true) failures.push("unrelated-conversation: 无关会话错误阻塞目标");
}

// 6. 点击联系前岗位身份已变：拒绝点击。
expect(
  "contact-identity-switch",
  () =>
    run({ card: { ...card, job_id: "job-other" }, chat: chatState() }, {
      action: "contact",
      identity: IDENTITY,
    }),
  "Job changed"
);

// 7. 发送按钮可用时点击一次。
{
  const { result, context } = run({ card, chat: chatState({ draft: "您好，我想应聘该岗位。" }) }, {
    action: "custom",
    text: "您好，我想应聘该岗位。",
    identity: IDENTITY,
  });
  if (result?.clicked !== true || context.clicked !== true) failures.push("custom: 未确认点击发送");
}

expect('missing-conversation-reply', () => run({ card, chat: chatState({draft:'正文', messages:[
  {sender:IDENTITY.uid, recipient:SELF, conversation_id:'', text:'可以聊聊'}
]}) }, {action:'custom', text:'正文', identity:IDENTITY}), 'Message identity incomplete');

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("guard-contract-ok");
