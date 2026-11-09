(a) => {
  // 读取函数由调用方把源码传进来，必须在本页面作用域里重建；
  // 页面 evaluate 的回调之间不共享闭包。
  const readState = new Function('return (' + a.reader + ')')();
  const sameTarget = (selected, identity) =>
    !!selected &&
    selected.job_id === identity.job_id &&
    selected.boss_id === identity.boss_id &&
    String(selected.uid) === String(identity.uid) &&
    (!identity.conversation_id ||
      String(selected.conversation_id) === String(identity.conversation_id));
  const inConversation = (message, identity) =>
    String(message.conversation_id) === String(identity.conversation_id);

  if (a.action === 'contact') {
    // 聊天历史在 chat 页核对；此回调只能读取 job 页。
    const card = readState({ self: a.self, kind: 'card' });
    if (
      card.job_id !== a.identity.job_id ||
      card.company !== a.identity.company ||
      card.boss_id !== a.identity.boss_id
    ) {
      throw Error('Job changed before click');
    }
    if (card.can_add_friend !== true) throw Error('Not a first contact');
    const button = document.querySelector(location.pathname.startsWith('/job_detail/')
      ? '.job-banner .btn-startchat' : '.job-detail-box .op-btn-chat');
    if (!button || !button.getClientRects().length || button.disabled) {
      throw Error('Contact button unavailable');
    }
    button.click();
    return { clicked: true };
  }

  if (a.action !== 'fill' && a.action !== 'custom') throw Error('Unknown native action');

  const validate = () => {
    const state = readState({ self: a.self, kind: 'chat' });
    if (String(state.platform_user_id) !== String(a.self)) throw Error('Account mismatch');
    if (!sameTarget(state.selected, a.identity)) throw Error('Recipient changed before click');
    if (!state.history_complete) throw Error('Platform history incomplete');
    if (!state.editor_present || !state.messages_loaded) throw Error('Conversation not ready');
    if (
      state.selected.conversation_id &&
      String(state.editor_id) !== String(state.selected.conversation_id)
    ) {
      throw Error('Editor does not match conversation');
    }
    if (state.draft !== (a.action === 'fill' ? '' : a.text)) throw Error('Draft changed');
    if ((state.messages || []).some(message => !message.conversation_id)) {
      throw Error('Message identity incomplete');
    }
    const messages = (state.messages || []).filter((message) => inConversation(message, a.identity));
    if (messages.some((m) => String(m.sender) === String(a.identity.uid))) {
      throw Error('Incoming reply present');
    }
    if (messages.some((m) => m.text === a.text)) throw Error('Existing message or reply');
    return state;
  };

  validate();

  if (a.action === 'fill') {
    const editor = document.querySelector('#chat-input');
    if (!editor || !editor.isContentEditable) throw Error('Editor unavailable');
    editor.focus();
    validate();
    if (document.activeElement !== editor || !editor.isContentEditable) {
      throw Error('Editor changed after focus');
    }
    if (!document.execCommand('insertText', false, a.text)) throw Error('Draft insertion failed');
    return { filled: true };
  }

  const button = document.querySelector('.chat-conversation .btn-send');
  if (
    !button ||
    !button.getClientRects().length ||
    button.disabled ||
    button.getAttribute('aria-disabled') === 'true' ||
    button.classList.contains('disabled') ||
    button.classList.contains('unable')
  ) {
    throw Error('Send disabled');
  }
  button.click();
  return { clicked: true };
}
