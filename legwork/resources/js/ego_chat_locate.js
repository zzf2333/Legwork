async (a) => {
  // 会话列表是虚拟滚动：DOM 只渲染可视窗口，目标会话不在窗口内时必须滚动查找。
  // 这里按 job/boss 身份匹配：猎头代招的岗位，会话里的公司名是猎头公司（例如「BOSS 直聘猎头」），
  // 与岗位详情页的公司名不同，把公司名当必要条件会永远定位不到。
  const matches = () => {
    const hits = [...document.querySelectorAll('.friend-content')].filter((node) => {
      const source = node.parentElement?.__vue__?.$props?.source;
      return (
        node.getClientRects().length &&
        source?.encryptJobId === a.identity.job_id &&
        source?.encryptBossId === a.identity.boss_id
      );
    });
    if (hits.length > 1) throw Error('Target contact duplicated in list');
    return hits;
  };
  const list = document.querySelector('.user-list-content');
  if (!list) throw Error('Contact scroll container unavailable');
  const settle = () => new Promise((resolve) => setTimeout(resolve, a.wait_ms ?? 400));

  list.scrollTop = 0; // 新会话排在首位，先从头找
  await settle();
  for (let step = 0; step <= (a.max_steps ?? 60); step += 1) {
    const hits = matches();
    if (hits.length === 1) {
      if (a.dry_run === true) return { found: true, step };
      hits[0].click();
      return { found: true, step };
    }
    const from = list.scrollTop;
    list.scrollTop = Math.min(from + list.clientHeight, list.scrollHeight);
    if (list.scrollTop === from) break;
    await settle();
  }
  throw Error('Target contact not found in list');
}
