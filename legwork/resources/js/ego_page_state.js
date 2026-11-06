(a) => {
  if (location.origin !== 'https://www.zhipin.com') throw Error('Wrong origin');
  if (/环境存在风险|安全验证|账号异常|验证后继续/.test(document.body.innerText)) throw Error('Visible platform restriction');

  const card = () => {
    const pathId = location.pathname.match(/^\/job_detail\/([A-Za-z0-9_-]+)\.html$/)?.[1];
    if (pathId) {
      const info = window._jobInfo;
      if (window._PAGE?.isLogin !== true || String(window._PAGE?.uid) !== String(a.self)) {
        throw Error('Account mismatch');
      }
      if (!info || info.job_id !== pathId || !info.user_id || !info.company) {
        throw Error('Direct detail identity unavailable');
      }
      const description = document.querySelector('.job-sec-text')?.innerText.trim();
      if (!description) throw Error('Job detail unavailable');
      const button = document.querySelector('.job-banner .btn-startchat');
      return {
        job_id: info.job_id,
        company: info.company,
        boss_id: String(info.user_id),
        boss_name: [...(document.querySelector('.job-boss-info h2')?.childNodes || [])]
          .filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim(),
        title: info.job_name,
        salary: info.job_salary || '',
        address: document.querySelector('.location-address')?.innerText.trim() || '',
        description,
        can_add_friend: button?.getAttribute('data-isfriend') === 'false' &&
          button.innerText.trim() === '立即沟通' && !!button.getClientRects().length,
      };
    }
    const page = document.querySelector('.page-jobs-main')?.__vue__;
    if (!page || page.jobDetailLoading || !page.jobDetail?.jobInfo) throw Error('Job detail unavailable');
    const detail = page.jobDetail;
    const info = detail.jobInfo;
    const boss = detail.bossInfo;
    const brand = detail.brandComInfo;
    if (!boss || !brand) throw Error('Job detail incomplete');
    if (String(page.$store?.state?.userInfo?.userId) !== String(a.self)) throw Error('Account mismatch');
    const button = document.querySelector('.job-detail-box .op-btn-chat');
    return {
      job_id: info.encryptId,
      company: brand.brandName,
      boss_id: String(info.encryptUserId || ''),
      boss_name: boss.name,
      title: info.jobName,
      salary: info.salaryDesc,
      address: info.address,
      description: info.postDescription,
      can_add_friend:
        detail.relationInfo?.beFriend === false &&
        info.invalidStatus === false &&
        button?.innerText.trim() === '立即沟通',
    };
  };

  const chat = () => {
    const first = document.querySelector('.friend-content')?.parentElement?.__vue__;
    let list = first;
    while (list && !(list.$options.name === 'boss-list' && Array.isArray(list.fullList))) list = list.$parent;
    if (!list || String(first.$store?.state?.userInfo?.userId) !== String(a.self)) throw Error('Contact list unavailable');
    const take = (source) => ({
      job_id: source?.encryptJobId ?? null,
      company: source?.brandName ?? null,
      boss_id: source?.encryptBossId ?? null,
      uid: source?.uid == null ? null : String(source.uid),
      conversation_id: source?.uniqueId ?? null,
    });
    const selectedNodes = document.querySelectorAll('.friend-content.selected');
    const input = document.querySelector('#chat-input');
    const record = document.querySelector('.chat-record')?.__vue__;
    const messages = [...document.querySelectorAll('.message-item')].map((node) => {
      const message = node.__vue__?.$props?.message;
      if (!message) throw Error('Unknown message shape');
      return {
        mid: String(message.msgId ?? message.mid ?? ''),
        conversation_id: String(message.uniqueId ?? message.conversationId ?? ''),
        sender: String(message.fromId),
        recipient: String(message.toId),
        text: message.text || '',
        kind: message.bodyType === 8 && message.jobDesc ? 'job_card' : 'text',
        delivered:
          message.received === true ||
          [...node.querySelectorAll('.message-status.status-delivery')].some(
            (label) => label.getClientRects().length && label.innerText.trim() === '送达'
          ),
      };
    });
    const labels = [...document.querySelectorAll('.label-name')];
    const all = labels.some(
      (node) => node.innerText.trim() === '全部' && node.parentElement.classList.contains('selected')
    );
    const search = document.querySelector('input[placeholder="搜索30天内的联系人"]');
    return {
      platform_user_id: String(first.$store.state.userInfo.userId),
      history_complete:
        all && !search?.value.trim() && !list.isLoading && list.bottomText === '没有更多了',
      contacts: list.fullList.map(take),
      selected: selectedNodes.length === 1 ? take(selectedNodes[0].parentElement.__vue__.$props.source) : null,
      draft: input?.innerText ?? '',
      editor_present: input?.isContentEditable === true,
      editor_id: input?.parentElement?.__vue__?.$props?.uniqueId ?? null,
      messages_loaded: !!record && record.loading === false,
      messages,
    };
  };

  if (a.kind === 'card') return card();
  if (a.kind === 'chat') return chat();
  throw Error('Unknown read kind');
}
