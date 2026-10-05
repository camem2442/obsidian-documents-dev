/** In-app confirm/prompt dialogs (replaces window.confirm / window.prompt). */
const DISMISS_PREFIX = 'exam-processor.dismiss.';

function examDismissed(key) {
  if (!key) return false;
  try { return localStorage.getItem(DISMISS_PREFIX + key) === '1'; } catch { return false; }
}

function examSetDismissed(key, on) {
  if (!key) return;
  try {
    if (on) localStorage.setItem(DISMISS_PREFIX + key, '1');
    else localStorage.removeItem(DISMISS_PREFIX + key);
  } catch { /* private mode */ }
}

function examConfirm({
  title = '',
  message = '',
  ok = '확인',
  cancel = '취소',
  dismissKey = '',
  dismissLabel = '다시 보지 않기',
} = {}) {
  return new Promise((resolve) => {
    if (dismissKey && examDismissed(dismissKey)) {
      resolve(true);
      return;
    }
    const dlg = document.getElementById('confirm-dialog');
    const form = document.getElementById('confirm-form');
    const titleEl = document.getElementById('confirm-title');
    const messageEl = document.getElementById('confirm-message');
    const okBtn = document.getElementById('confirm-ok');
    const cancelBtn = document.getElementById('confirm-cancel');
    const dismissWrap = document.getElementById('confirm-dismiss-wrap');
    const dismissBox = document.getElementById('confirm-dismiss');
    const dismissText = dismissWrap?.querySelector('.exam-dialog-option-text');
    if (!dlg || !form) {
      resolve(window.confirm(message || title));
      return;
    }
    titleEl.textContent = title;
    messageEl.textContent = message;
    okBtn.textContent = ok;
    cancelBtn.textContent = cancel;
    if (dismissKey) {
      dismissWrap.hidden = false;
      if (dismissText) dismissText.textContent = dismissLabel;
      dismissBox.checked = false;
    } else {
      dismissWrap.hidden = true;
    }
    const finish = (value) => {
      dlg.close();
      form.onsubmit = null;
      cancelBtn.onclick = null;
      dlg.oncancel = null;
      resolve(value);
    };
    form.onsubmit = (e) => {
      e.preventDefault();
      if (dismissKey && dismissBox.checked) examSetDismissed(dismissKey, true);
      finish(true);
    };
    cancelBtn.onclick = () => finish(false);
    dlg.oncancel = (e) => {
      e.preventDefault();
      finish(false);
    };
    dlg.showModal();
    okBtn.focus();
  });
}

function examPrompt({
  title = '',
  message = '',
  label = '',
  placeholder = '',
  ok = '확인',
  cancel = '취소',
  minLength = 0,
  multiline = true,
  emptyDefault = '',
} = {}) {
  return new Promise((resolve) => {
    const dlg = document.getElementById('prompt-dialog');
    const form = document.getElementById('prompt-form');
    const input = document.getElementById('prompt-input');
    const err = document.getElementById('prompt-error');
    if (!dlg || !form || !input) {
      const fallback = window.prompt(message || title);
      resolve(fallback);
      return;
    }
    document.getElementById('prompt-title').textContent = title;
    document.getElementById('prompt-message').textContent = message;
    document.getElementById('prompt-label-text').textContent = label || '입력';
    input.placeholder = placeholder;
    input.value = '';
    input.rows = multiline ? 3 : 1;
    err.hidden = true;
    err.textContent = '';
    document.getElementById('prompt-ok').textContent = ok;
    document.getElementById('prompt-cancel').textContent = cancel;
    const finish = (value) => {
      dlg.close();
      form.onsubmit = null;
      document.getElementById('prompt-cancel').onclick = null;
      dlg.oncancel = null;
      resolve(value);
    };
    form.onsubmit = (e) => {
      e.preventDefault();
      let text = input.value.trim();
      if (!text && emptyDefault) text = emptyDefault;
      if (minLength && text.length < minLength) {
        err.hidden = false;
        err.textContent = `${minLength}자 이상 입력하세요.`;
        input.focus();
        return;
      }
      finish(text);
    };
    document.getElementById('prompt-cancel').onclick = () => finish(null);
    dlg.oncancel = (e) => {
      e.preventDefault();
      finish(null);
    };
    dlg.showModal();
    input.focus();
  });
}

window.examConfirm = examConfirm;
window.examPrompt = examPrompt;
window.examDismissed = examDismissed;
