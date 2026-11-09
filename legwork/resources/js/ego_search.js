// `search` 动作的脚本体：由 ego.py 的 _run 拼进任务脚本，作用域内有 job、chat、a、collect。
// 外层用 async 包裹，顶层 await 在 CommonJS 下也能通过 `node --check`。
return await (async () => {
  const current = new URL(await job.url());
  if (current.origin !== 'https://www.zhipin.com') throw Error('Wrong job page origin');
  if (/^\/job_detail\/[^/]+\.html$/.test(current.pathname)) {
    const url = 'https://www.zhipin.com/web/geek/jobs?'+new URLSearchParams({city:a.city_code,query:a.keyword});
    await job.goto(url, {timeout:25000});
    await job.waitForFunction(() => {
      const main=document.querySelector('.page-jobs-main')?.__vue__;
      return main?.loading===false && Array.isArray(main.jobList);
    }, undefined, {timeout:15000});
  }
  const before = await job.evaluate(collect, { self: a.self, city_code: a.city_code, action: 'read' });
  let watchKey = null;
  let list;
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
    list = await job.evaluate(collect, {self:a.self, city_code:a.city_code, action:'read'});
  } finally {
    if (watchKey) await job.evaluate(key => {
      globalThis[key]?.observer.disconnect();
      delete globalThis[key];
    }, watchKey);
  }
  let load_error = null;
  // 每轮滚动至少要新增一条岗位，所以步数上限就是 limit：到不了就停，不做无限滚动。
  for (let step = 0; step < a.limit && list.rows.length < a.limit; step++) {
    const previous = list.rows.length;
    try {
      await job.evaluate(() => {
        window.scrollTo(0, document.documentElement.scrollHeight);
      });
      await job.waitForFunction(({previous}) => {
        const main = document.querySelector('.page-jobs-main')?.__vue__;
        const done = main?.hasMore === false;
        return done || (main?.loading === false && Array.isArray(main.jobList) &&
          main.jobList.length > previous);
      }, {previous}, {timeout:10000});
    } catch (error) {
      load_error = '加载更多超时或失败：' + String((error && error.message) || error);
      break;
    }
    list = await job.evaluate(collect, {self:a.self, city_code:a.city_code, action:'read'});
    if (list.rows.length <= previous) break;
  }
  return { list, load_error };
})();
