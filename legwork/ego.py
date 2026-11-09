"""Ego 浏览器边界：读取页面状态、受控点击，以及离线替身。

真实平台操作只出现在本模块和随包 JS 资源中；首版显式依赖 ego-browser。
不自动切换空间、不绕过平台验证、不在结果不明时重试。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import re
import subprocess
import tempfile
import time
from importlib import resources
from pathlib import Path

BINARY = "ego-browser"
RESULT_MARKER = "BOSS_ASSISTANT_RESULT_SAVED"
RESULT_PLACEHOLDER = "__BOSS_ASSISTANT_RESULT_PATH__"
# 会话列表滚动查找上限：按每次滚动一屏计算，足够覆盖上万条会话的异常情况。
LOCATE_STEPS = 60


class BrowserError(RuntimeError):
    """浏览器操作失败；调用方保留占用并人工核对，不自动重试。"""

    code = "browser_error"


class BrowserBusy(BrowserError):
    """同一数据目录已有进程在操作浏览器。"""

    code = "browser_busy"


@contextlib.contextmanager
def browser_lock(data_dir: str | Path, timeout: float = 0.0):
    """本地进程互斥。只防本机并发，不是平台分布式锁，也不保证端到端恰好一次。"""
    path = Path(data_dir) / "browser.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BrowserBusy("同一数据目录已有进程在操作浏览器，等它结束后再试") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def resource_text(relative: str) -> str:
    return resources.files("legwork").joinpath("resources", relative).read_text(encoding="utf-8")


def resource_path(relative: str) -> Path:
    return Path(str(resources.files("legwork").joinpath("resources", relative)))


def _expected_identity(job: dict) -> dict:
    """发送前约定的目标身份，只来自运行快照，不采用页面当前选中的会话。"""
    return {
        "job_id": job["job_id"],
        "company": job["company"],
        "boss_id": job["boss_id"],
    }


def saved_job_url(job: dict) -> str:
    """发送只能使用与稳定岗位 ID 对应的已保存详情地址。"""
    job_id = str(job.get("job_id", ""))
    expected = f"https://www.zhipin.com/job_detail/{job_id}.html"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id) or job.get("job_url") != expected:
        raise BrowserError("保存的岗位链接与岗位 ID 不一致，停止定位")
    return expected


class EgoBrowser:
    """通过 `ego-browser nodejs` 执行随包 JS 资源。"""

    mode = "live"

    def __init__(self, config, *, binary: str = BINARY, timeout: float = 60.0):
        self.space = config.browser["space"]
        self.job_page = config.browser["job_page"]
        self.chat_page = config.browser["chat_page"]
        self.platform_user_id = config.platform_user_id
        self.city_code = config.city_code
        self.dedupe_scope = config.dedupe_scope
        self.binary = binary
        self.timeout = timeout
        self._state_js = resource_text("js/ego_page_state.js")
        self._guard_js = resource_text("js/ego_click_guard.js")
        self._collect_js = resource_text("js/ego_collect.js")
        self._locate_js = resource_text("js/ego_chat_locate.js")

    # -- 页面读取 -----------------------------------------------------------
    def card_state(self) -> dict:
        return self._run("card", body="return await job.evaluate(readState, {self:a.self, kind:'card'});")

    def chat_state(self) -> dict:
        return self._run("chat", body="return await chat.evaluate(readState, {self:a.self, kind:'chat'});")

    def load_history(self, max_batches: int = 30) -> dict:
        """通过页面滚动加载去重历史；到达明确终点才返回，无进展即停止。"""
        return self._run(
            "history",
            body="""
    for (let batch = 0; batch <= a.max_batches; batch++) {
      if (space.ownership !== 'agent') throw Error('Browser control lost');
      const state = await chat.evaluate(readState, {self:a.self, kind:'chat'});
      if (state.history_complete) return state;
      if (batch === a.max_batches) throw Error('History batch limit reached; history incomplete');
      await chat.evaluate(() => {
        const list = document.querySelector('.user-list-content');
        if (!list) throw Error('Contact scroll container unavailable');
        list.scrollTop = list.scrollHeight;
      });
      await chat.waitForFunction(({count, self, reader}) => {
        const read = new Function('return (' + reader + ')')();
        const state = read({self, kind:'chat'});
        return state.history_complete || state.contacts.length > count;
      }, {count:state.contacts.length, self:a.self, reader:a.reader}, {timeout:5000});
    }
    throw Error('History incomplete');""",
            max_batches=max_batches,
            reader=self._state_js,
        )

    def open_job(self, job: dict) -> dict:
        """在已有列表页里找到目标岗位并打开详情；不直接跳转详情 URL，也不假设页面结构不同。"""
        if job.get("source_keyword"):
            self.search(job["source_keyword"], self.city_code)
        return self._run(
            "open",
            job_id=job["job_id"],
            body="""
    const clicked = await job.evaluate(({ id }) => {
      const links = [...document.querySelectorAll('li.job-card-box')].filter(
        (node) => (node.querySelector('a.job-name')?.getAttribute('href') || '').includes('/job_detail/' + id + '.html')
      );
      const link = links.length === 1 ? links[0] : null;
      if (!link) return { found: false };
      link.click();
      return { found: true };
    }, { id: a.job_id });
    if (clicked?.found !== true) {
      throw new Error('目标岗位不在当前列表页：先按关键词搜索到该岗位，再重新发送');
    }
    await job.waitForFunction(({ id, reader, self }) => {
      const readState = new Function('return (' + reader + ')')();
      try {
        const card = readState({ self, kind: 'card' });
        return card.job_id === id;
      } catch (error) {
        return false;
      }
    }, { id: a.job_id, reader: a.reader, self: a.self }, { timeout: 20000 });
    return { url: await job.url() };""",
            reader=self._state_js,
        )

    def open_saved_job(self, target: dict) -> dict:
        url = saved_job_url(target)
        return self._run(
            "open_saved",
            url=url,
            identity=_expected_identity(target),
            reader=self._state_js,
            body="""
    const current = await job.url();
    if (current !== 'about:blank' && new URL(current).origin !== 'https://www.zhipin.com') {
      throw Error('Configured job page is not a BOSS page');
    }
    await job.goto(a.url, {timeout:25000});
    await job.waitForFunction(({self, reader}) => {
      const read = new Function('return (' + reader + ')')();
      try { return !!read({self, kind:'card'}).description; } catch { return false; }
    }, {self:a.self, reader:a.reader}, {timeout:15000});
    const card = await job.evaluate(readState, {self:a.self, kind:'card'});
    if (['job_id','boss_id','company'].some(k => card[k] !== a.identity[k])) {
      throw Error('Saved link target identity mismatch');
    }
    return {url:await job.url()};""",
        )

    # -- 列表导航 -----------------------------------------------------------
    def search(self, keyword: str, city_code: str) -> dict:
        return self._run(
            "search",
            body="""
    const current = new URL(await job.url());
    if (current.origin !== 'https://www.zhipin.com') throw Error('Wrong job page origin');
    if (/^\\/job_detail\\/[^/]+\\.html$/.test(current.pathname)) {
      const url = 'https://www.zhipin.com/web/geek/jobs?'+new URLSearchParams({city:a.city_code,query:a.keyword});
      await job.goto(url, {timeout:25000});
      await job.waitForFunction(() => {
        const main=document.querySelector('.page-jobs-main')?.__vue__;
        return main?.loading===false && Array.isArray(main.jobList);
      }, undefined, {timeout:15000});
    }
    const before = await job.evaluate(collect, { self: a.self, city_code: a.city_code, action: 'read' });
    let watchKey = null;
    try {
      if (before.query !== a.keyword || !before.search_active) {
        watchKey = '__bossSearch_' + Math.random().toString(36).slice(2);
        await job.evaluate(key => {
          const state = {complete:false};
          state.observer = new PerformanceObserver(list => {
            if (list.getEntries().some(e => e.responseEnd > 0 &&
                new URL(e.name).pathname === '/wapi/zpgeek/search/joblist.json')) state.complete = true;
          });
          globalThis[key] = state;
          state.observer.observe({type:'resource'});
        }, watchKey);
        await job.fill('loc=css:input[placeholder="搜索职位、公司"]', a.keyword);
        await job.press('loc=css:input[placeholder="搜索职位、公司"]', 'Enter');
      }
      await job.waitForFunction(({keyword, watchKey}) => {
        const query = new URL(location.href).searchParams.get('query');
        const main = document.querySelector('.page-jobs-main')?.__vue__;
        const completed = watchKey === null || globalThis[watchKey]?.complete === true;
        return completed && query === keyword && main?.loading === false && Array.isArray(main.jobList) &&
          document.querySelectorAll('li.job-card-box').length === main.jobList.length;
      }, {keyword:a.keyword, watchKey}, {timeout:15000});
      return { list: await job.evaluate(collect, {self:a.self, city_code:a.city_code, action:'read'}) };
    } finally {
      if (watchKey) await job.evaluate(key => {
        globalThis[key]?.observer.disconnect();
        delete globalThis[key];
      }, watchKey);
    }""",
            keyword=keyword,
            city_code=city_code,
        )["list"]

    # -- 受控点击 -----------------------------------------------------------
    def click_contact(self, job: dict) -> dict:
        """点击前原子核对目标岗位；点击后按身份滚动查找并选中目标会话。"""
        return self._run(
            "contact",
            body="""
    const before = await chat.evaluate(readState, {self:a.self, kind:'chat'});
    if (!before.history_complete || before.draft.trim()) throw Error('Chat history or draft changed');
    const fields = {job:'job_id', boss:'boss_id', company:'company'};
    if (before.contacts.some(c => a.scopes.some(scope => c[fields[scope]] === a.identity[fields[scope]]))) {
      throw Error('Existing contact');
    }
    const clicked = await job.evaluate(clickGuard, {
      self: a.self,
      reader: a.reader,
      action: 'contact',
      identity: a.identity,
    });
    if (clicked?.clicked !== true) throw new Error('点击未确认');
    await chat.waitForFunction(({ self, identity, reader }) => {
      const read = new Function('return (' + reader + ')')();
      const state = read({self, kind:'chat'});
      return state.contacts.some(c => c.job_id === identity.job_id && c.boss_id === identity.boss_id);
    }, {self:a.self, identity:a.identity, reader:a.reader}, {timeout:20000});
    const located = await chat.evaluate(locateContact, {
      identity: a.identity, max_steps: a.locate_steps,
    });
    if (located?.found !== true) throw Error('Target contact not located');
    await chat.waitForFunction(({ self, identity, reader }) => {
      const read = new Function('return (' + reader + ')')();
      const state = read({self, kind:'chat'}), c=state.selected;
      return c?.job_id===identity.job_id && c?.boss_id===identity.boss_id &&
        state.messages_loaded && state.editor_present && String(state.editor_id)===String(c.conversation_id) &&
        state.messages.some(m => m.mid && m.conversation_id===c.conversation_id &&
          m.sender===String(self) && m.recipient===String(c.uid) && m.delivered);
    }, {self:a.self, identity:a.identity, reader:a.reader}, {timeout:20000});
    return { clicked: true, locate_step: located.step };""",
            identity=_expected_identity(job),
            scopes=list(self.dedupe_scope),
            reader=self._state_js,
            locate_steps=LOCATE_STEPS,
        )

    def open_contact(self, job: dict) -> dict:
        """已有会话：不点「立即沟通」，只按身份滚动查找、选中并确认可填写。"""
        return self._run(
            "open_contact",
            body="""
    const before = await chat.evaluate(readState, {self:a.self, kind:'chat'});
    if (!before.history_complete) throw Error('Chat history incomplete');
    if (before.draft.trim()) throw Error('Chat draft changed');
    const fields = {job:'job_id', boss:'boss_id', company:'company'};
    if (!before.contacts.some(c => a.scopes.some(scope => c[fields[scope]] === a.identity[fields[scope]]))) {
      throw Error('Contact missing');
    }
    const located = await chat.evaluate(locateContact, {
      identity: a.identity, max_steps: a.locate_steps,
    });
    if (located?.found !== true) throw Error('Target contact not located');
    await chat.waitForFunction(({ self, identity, reader }) => {
      const read = new Function('return (' + reader + ')')();
      const state = read({self, kind:'chat'}), c=state.selected;
      return c?.job_id===identity.job_id && c?.boss_id===identity.boss_id &&
        state.messages_loaded && state.editor_present && String(state.editor_id)===String(c.conversation_id);
    }, {self:a.self, identity:a.identity, reader:a.reader}, {timeout:20000});
    return { located_step: located.step };""",
            identity=_expected_identity(job),
            scopes=list(self.dedupe_scope),
            reader=self._state_js,
            locate_steps=LOCATE_STEPS,
        )

    def fill_draft(self, text: str, identity: dict) -> None:
        self._run(
            "fill",
            body="""
    const filled = await chat.evaluate(clickGuard, {
      self: a.self, reader: a.reader, action: 'fill', text: a.text, identity: a.identity,
    });
    if (filled?.filled !== true) throw new Error('草稿填充未确认');
    return { filled: true };""",
            text=text,
            identity=identity,
            reader=self._state_js,
        )

    def click_send(self, text: str, identity: dict) -> None:
        """点击发送，并等到目标会话出现已送达的定制正文；读不到就交给调用方判 uncertain。"""
        self._run(
            "send",
            body="""
    const sent = await chat.evaluate(clickGuard, {
      self: a.self, reader: a.reader, action: 'custom', text: a.text, identity: a.identity,
    });
    if (sent?.clicked !== true) throw new Error('发送点击未确认');
    await chat.waitForFunction(({ self, identity, text, reader }) => {
      const readState = new Function('return (' + reader + ')')();
      try {
        const state = readState({ self, kind: 'chat' });
        if (!state.selected || String(state.selected.uid) !== String(identity.uid)) return false;
        return (state.messages || []).some(
          (message) =>
            message.text === text &&
            String(message.sender) === String(self) &&
            String(message.recipient) === String(identity.uid) &&
            message.delivered === true &&
            String(message.conversation_id) === String(identity.conversation_id) &&
            !!message.mid
        );
      } catch (error) {
        return false;
      }
    }, { self: a.self, identity: a.identity, text: a.text, reader: a.reader }, { timeout: 20000 });
    return { clicked: true };""",
            text=text,
            identity=identity,
            reader=self._state_js,
        )

    # -- 内部 ---------------------------------------------------------------

    def _run(self, action: str, *, body: str, **arguments) -> dict:
        payload = json.dumps(
            {"action": action, "self": self.platform_user_id, **arguments}, ensure_ascii=False
        )
        script = f"""const space = await taskSpace({json.dumps(self.space)});
const job = space.page({json.dumps(self.job_page)});
const chat = space.page({json.dumps(self.chat_page)});
if (space.ownership !== 'agent') throw Error('Browser space not agent controlled');
const readState = {self._state_js};
const clickGuard = {self._guard_js};
const locateContact = {self._locate_js};
const collect = {self._collect_js};
const a = {payload};
let result;
try {{
  result = await (async () => {{{body}
  }})();
}} catch (error) {{
  result = {{ failure: {{ code: 'ego_action_failed', message: String((error && error.message) || error) }} }};
}}
const fs = await import('node:fs/promises');
await fs.writeFile({RESULT_PLACEHOLDER}, JSON.stringify(result), {{ mode: 0o600, flag: 'wx' }});
console.log('{RESULT_MARKER}');
"""
        with tempfile.TemporaryDirectory(prefix="legwork-ego-") as folder:
            result_file = Path(folder) / "result.json"
            script = script.replace(RESULT_PLACEHOLDER, json.dumps(str(result_file)))
            try:
                process = subprocess.run(
                    [self.binary, "nodejs"],
                    input=script,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise BrowserError(
                    "找不到 ego-browser：先安装 ego lite 并执行 `ego-browser skill` 阅读用法"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise BrowserError("ego-browser 超时：保留占用并人工核对，不自动重试") from exc
            if process.returncode != 0:
                tail = (process.stderr or process.stdout).strip().splitlines()[-3:]
                raise BrowserError("ego-browser 执行失败：{}".format(" / ".join(tail) or "无输出"))
            if (process.stdout + "\n" + process.stderr).splitlines().count(RESULT_MARKER) != 1:
                raise BrowserError("ego 结果标记缺失或重复：按失败处理，不重试")
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BrowserError("ego 结果无法读取：按失败处理，不重试") from exc
        if isinstance(result, dict) and "failure" in result:
            raise BrowserError(str(result["failure"].get("message") or "ego 操作失败"))
        return result
