// 在独立 Node 上下文里执行随包会话定位脚本，验证虚拟滚动列表里能滚动找到目标会话。
// 由 tests/test_browser_js.py 以 `node tests/js/locate_contract.mjs` 调用；不接触真实浏览器。
import { readFileSync } from "node:fs";
import vm from "node:vm";

const locateSource = readFileSync(
  new URL("../../legwork/resources/js/ego_chat_locate.js", import.meta.url),
  "utf8"
);

const TARGET = { job_id: "job-target", company: "示例目标科技", boss_id: "boss-target" };
const ITEM_HEIGHT = 80;
const WINDOW_HEIGHT = 800; // 只渲染 10 条，模拟虚拟滚动

function sourceFor(index) {
  if (index === "target") {
    return {
      encryptJobId: TARGET.job_id,
      encryptBossId: TARGET.boss_id,
      brandName: TARGET.company,
      uid: "uid-target",
      uniqueId: "conv-target",
    };
  }
  return {
    encryptJobId: "job-" + index,
    encryptBossId: "boss-" + index,
    brandName: "示例公司" + index,
    uid: "uid-" + index,
    uniqueId: "conv-" + index,
  };
}

function pageFor({ items, scrollTop = 0, duplicated = false }) {
  const list = {
    scrollTop,
    clientHeight: WINDOW_HEIGHT,
    scrollHeight: items.length * ITEM_HEIGHT,
    querySelector: null,
  };
  const clicks = [];
  const render = () => {
    if (list.scrollTop > list.scrollHeight - list.clientHeight) {
      list.scrollTop = Math.max(0, list.scrollHeight - list.clientHeight);
    }
    const first = Math.floor(list.scrollTop / ITEM_HEIGHT);
    const last = first + Math.ceil(WINDOW_HEIGHT / ITEM_HEIGHT);
    const rendered = [];
    for (let i = first; i < Math.min(last, items.length); i += 1) {
      rendered.push(node(sourceFor(items[i]), clicks, i));
    }
    if (duplicated && items.slice(first, last).includes("target")) {
      rendered.push(node(sourceFor("target"), clicks, -1));
    }
    return rendered;
  };
  let rendered = render();
  const document = {
    querySelector(selector) {
      if (selector === ".user-list-content") {
        return {
          get scrollTop() {
            return list.scrollTop;
          },
          set scrollTop(value) {
            list.scrollTop = value;
            rendered = render();
          },
          get clientHeight() {
            return list.clientHeight;
          },
          get scrollHeight() {
            return list.scrollHeight;
          },
        };
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".friend-content") return rendered;
      return [];
    },
  };
  return { document, clicks, list };
}

function node(source, clicks, index) {
  return {
    getClientRects: () => [1],
    parentElement: { __vue__: { $props: { source } } },
    click() {
      clicks.push(index);
    },
  };
}

async function run(options, args) {
  const { document, clicks } = pageFor(options);
  const sandbox = { document, Error, setTimeout };
  const locate = vm.runInNewContext("(" + locateSource + ")", sandbox);
  const result = await locate({ identity: TARGET, wait_ms: 0, max_steps: 80, ...args });
  return { result, clicks };
}

const failures = [];

async function expectFailure(name, options, expected) {
  try {
    await run(options, {});
    failures.push(name + ": 没有按预期失败");
  } catch (error) {
    if (!String(error.message).includes(expected)) {
      failures.push(name + ": 期望包含 " + expected + "，实际 " + error.message);
    }
  }
}

// 1. 目标会话在列表深处，超出初始渲染窗口：必须滚动后找到并点击。
{
  const items = Array.from({ length: 400 }, (_, i) => "other-" + i);
  items[350] = "target";
  const { result, clicks } = await run({ items });
  if (result?.found !== true) failures.push("deep-target: 未找到目标会话");
  if (clicks.length !== 1 || clicks[0] !== 350) failures.push("deep-target: 点击了错误会话 " + clicks.join(","));
  if (result?.step < 2) failures.push("deep-target: 未发生滚动查找");
}

// 2. 列表停在很靠下的位置，但目标会话是第一位的新会话：从头开始查找。
{
  const items = ["target", ...Array.from({ length: 300 }, (_, i) => "other-" + i)];
  const { result, clicks } = await run({ items, scrollTop: 20000 });
  if (result?.found !== true || clicks[0] !== 0) failures.push("top-target: 未定位到首位会话");
  if (result?.step !== 0) failures.push("top-target: 期望无需滚动，实际 step=" + result?.step);
}

// 3. 同一身份在窗口里出现两次：拒绝点击，不猜哪一个。
await expectFailure("duplicated", { items: ["target", "other-1"], duplicated: true }, "duplicated in list");

// 4. 目标不在列表：滚动到底后失败，不无限循环。
await expectFailure(
  "missing",
  { items: Array.from({ length: 200 }, (_, i) => "other-" + i) },
  "Target contact not found"
);

// 5. 试运行不点击会话，只确认能定位。
{
  const { result, clicks } = await run({ items: ["other-1", "target"] }, { dry_run: true });
  if (result?.found !== true || clicks.length !== 0) failures.push("dry-run: 试运行不应点击会话");
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("locate-contract-ok");
