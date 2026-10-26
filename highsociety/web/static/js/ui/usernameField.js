// A username box that checks availability as you type: a spinner while it
// asks the server (/api/auth/username/check), a green tick when the name is
// free, a red cross with the reason (and a free "name 2"-style suggestion
// to use in one click) when it isn't. Used by the sign-in screen and the
// Account rename field. The check is only advisory -- the claim/rename
// endpoints re-check before writing -- so a failed check never blocks
// submitting.
//
// Markup it expects (see index.html's #login-username-field):
//   field   .username-field wrapper; its data-state drives the icons/border
//   input   the text input inside it
//   status  a <p> under the row, one line tall whether empty or not
//   count   optional "7/24" counter beside the status
//   submit  the button that submits; disabled while the name is unusable
import { fetchJSON } from '../lobby/lobby.js';

const CHECK_DELAY_MS = 300;
const MAX_LENGTH = 24;

export function attachUsernameField({ field, input, status, count, submit, current = () => null }) {
  let timer = null;
  let seq = 0;            // ignores responses to anything but the latest keystroke
  let pending = null;     // the in-flight check, for submit to await
  let state = '';         // '' | 'checking' | 'ok' | 'bad'

  function render(nextState, message, suggestion) {
    state = nextState;
    field.dataset.state = nextState;
    input.setAttribute('aria-invalid', nextState === 'bad' ? 'true' : 'false');
    status.className = `username-status${nextState ? ` username-status-${nextState}` : ''}`;
    status.textContent = message || '';
    if (suggestion) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'username-suggestion';
      btn.textContent = `Use “${suggestion}”`;
      btn.addEventListener('click', () => { setKnownAvailable(suggestion); input.focus(); });
      status.append(' ', btn);
    }
    if (submit) submit.disabled = nextState === 'bad' || !input.value.trim();
  }

  function renderCount() {
    if (!count) return;
    const n = input.value.length;
    count.textContent = n ? `${n}/${MAX_LENGTH}` : '';
    count.classList.toggle('username-count-near', n >= MAX_LENGTH - 3);
  }

  async function runCheck(name, mySeq) {
    const own = current();
    const query = new URLSearchParams({ username: name });
    if (own) query.set('current', own);
    try {
      const result = await fetchJSON(`/api/auth/username/check?${query}`);
      if (mySeq !== seq) return;
      if (result.available) {
        render('ok', own && name === own ? 'This is your current username' : 'Available');
      } else {
        render('bad', result.error || 'Already taken.', result.suggestion);
      }
    } catch (e) {
      // Couldn't ask -- say nothing rather than guess; the claim will tell.
      if (mySeq === seq) render('', '');
    }
  }

  function check() {
    clearTimeout(timer);
    renderCount();
    const name = input.value.trim();
    seq += 1;
    if (!name) {
      pending = null;
      render('', `Up to ${MAX_LENGTH} characters`);
      if (submit) submit.disabled = true;
      return;
    }
    render('checking', 'Checking…');
    const mySeq = seq;
    pending = new Promise((resolve) => {
      timer = setTimeout(() => runCheck(name, mySeq).then(resolve), CHECK_DELAY_MS);
    });
  }

  // A name the server just handed out as free (a guest suggestion, a
  // Google pre-fill) -- no need to ask again.
  function setKnownAvailable(name) {
    clearTimeout(timer);
    seq += 1;
    pending = null;
    input.value = name;
    renderCount();
    if (name) render('ok', 'Available');
    else check();
  }

  // The claim itself came back taken (someone grabbed it in between).
  function showTaken(suggestion) {
    clearTimeout(timer);
    seq += 1;
    pending = null;
    render('bad', 'Already taken.', suggestion);
  }

  // For submit: true once the current text is known usable (or the check
  // couldn't run, in which case the server decides).
  async function ready() {
    if (state === 'checking') {
      clearTimeout(timer);
      const name = input.value.trim();
      const mySeq = seq;
      await runCheck(name, mySeq);
    } else if (pending) {
      await pending;
    }
    return state !== 'bad' && !!input.value.trim();
  }

  function setBusy(busy, label) {
    if (!submit) return;
    submit.classList.toggle('is-busy', busy);
    submit.disabled = busy || state === 'bad' || !input.value.trim();
    if (label) submit.textContent = label;
    input.readOnly = busy;
  }

  input.addEventListener('input', check);

  return { check, setKnownAvailable, showTaken, ready, setBusy };
}
