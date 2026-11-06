// Player/spectator chat: message bubbles, send handlers, and keeping new
// messages noticeable when you've scrolled up or (on a phone, where chat sits
// at the bottom of the page) aren't looking at it.
import { $, hide, show } from '../utils/dom.js';
import { sendIfOpen } from '../network/websocket.js';
import { nameOf } from '../game/gameState.js';

// How close to the bottom still counts as "reading the latest".
const AT_BOTTOM_PX = 40;

// Enough distinct names that a table of five rarely repeats one, all dark
// enough to read on the light bubble background.
const SENDER_COLORS = ['#8f6a22', '#1f7a4d', '#a3302a', '#2d5f8b', '#6f4a8e', '#a0522d', '#2f7f7a'];

function senderColor(username) {
  let hash = 0;
  for (let i = 0; i < username.length; i += 1) hash = (hash * 31 + username.charCodeAt(i)) % 1009;
  return SENDER_COLORS[hash % SENDER_COLORS.length];
}

// logId -> the sender of the last bubble in that log, so a run of messages from
// one person only names them once.
const lastSender = {};
let unread = 0;

function isAtBottom(log) {
  return log.scrollHeight - log.scrollTop - log.clientHeight < AT_BOTTOM_PX;
}

function pillFor(logId) {
  return document.querySelector(`[data-chat-pill="${logId}"]`);
}

function isChatOnScreen(log) {
  if (log.offsetParent === null) return false;
  const rect = log.getBoundingClientRect();
  return rect.top < window.innerHeight && rect.bottom > 0;
}

function renderUnread() {
  const jump = $('chat-jump');
  if (unread > 0) {
    $('chat-jump-count').textContent = unread > 99 ? '99+' : String(unread);
    show(jump);
  } else {
    hide(jump);
  }
}

function scrollToLatest(log) {
  log.scrollTop = log.scrollHeight;
  hide(pillFor(log.id));
}

// Follows the conversation if you were already at the latest message. If you've
// scrolled up to read something, leaves you there and says there's more.
function placeInLog(log, element, { alwaysFollow = false } = {}) {
  const follow = alwaysFollow || isAtBottom(log);
  log.appendChild(element);
  if (follow) scrollToLatest(log);
  else show(pillFor(log.id));
}

function appendBubble(logId, { from, text, mine, tag }) {
  const log = $(logId);
  const row = document.createElement('div');
  row.className = `chat-msg${mine ? ' mine' : ''}`;
  if (lastSender[logId] === from) {
    row.classList.add('same-sender');
  } else if (!mine) {
    const sender = document.createElement('span');
    sender.className = 'chat-sender';
    sender.style.color = senderColor(from);
    sender.textContent = nameOf(from);
    if (tag) {
      const tagEl = document.createElement('span');
      tagEl.className = 'chat-tag';
      tagEl.textContent = ` · ${tag}`;
      sender.appendChild(tagEl);
    }
    row.appendChild(sender);
  }
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  bubble.textContent = text; // always text, never markup: this came from another player
  row.appendChild(bubble);
  lastSender[logId] = from;
  placeInLog(log, row, { alwaysFollow: mine });
}

function appendNote(logId, text) {
  const note = document.createElement('div');
  note.className = 'chat-note';
  note.textContent = text;
  lastSender[logId] = null;
  placeInLog($(logId), note, { alwaysFollow: true });
}

// A relayed message's prompt is "💬 sender[ (spectators only)]: text" -- plain
// text for the CLI clients that just print it. Peel that prefix back off.
function messageBody(prompt, from) {
  const prefix = `💬 ${from}`;
  if (!from || !prompt.startsWith(prefix)) return prompt;
  const colon = prompt.indexOf(': ', prefix.length);
  return colon === -1 ? prompt : prompt.slice(colon + 2);
}

// Called for every CHAT message received over the wire.
export function receiveChatMessage(logId, msg) {
  const from = msg.from_user || '';
  const toSpectators = msg['to_user(s)'] === 'spectators';
  appendBubble(logId, {
    from,
    text: messageBody(msg.prompt || '', from),
    mine: false,
    tag: toSpectators ? 'spectators only' : null,
  });
  if (!isChatOnScreen($(logId))) {
    unread += 1;
    renderUnread();
  }
}

export function resetChat() {
  Object.keys(lastSender).forEach((id) => { delete lastSender[id]; });
  unread = 0;
  renderUnread();
  document.querySelectorAll('[data-chat-pill]').forEach(hide);
}

function send(logId, inputId, payload, tag) {
  const input = $(inputId);
  const text = input.value.trim();
  if (!text) return;
  if (!sendIfOpen({ message_type: 'CHAT', prompt: text, ...payload })) {
    // Kept in the box so it isn't lost: send it again once reconnected.
    appendNote(logId, 'Not connected, so that message wasn\'t sent.');
    return;
  }
  // A CHAT message is never echoed back to its own sender over the wire (see
  // docs/PLAYING.md), so the sender's own bubble is added here.
  appendBubble(logId, { from: '__me__', text, mine: true, tag });
  input.value = '';
}

export function onSpecChatSend() {
  // See web_server.py's _spectator_chat_listener: "spectators" reaches only
  // other spectators, anything else (including omitting the field) reaches
  // everyone — players included.
  const target = $('spec-chat-target-toggle').checked ? 'spectators' : 'all';
  send('spec-chat-log', 'spec-chat-input', { target }, target === 'spectators' ? 'spectators only' : null);
}

export function onPlayerChatSend() {
  // Reaches every other player + all spectators (see web_server.py's
  // _relay_player_chat) — no "target" selector for players, unlike
  // spectators, since chatting to a subset of the table doesn't make sense
  // from a player's own seat.
  send('player-chat-log', 'player-chat-input', {});
}

// Wired once at startup from app.js.
export function initChat() {
  ['player-chat-log', 'spec-chat-log'].forEach((id) => {
    const log = $(id);
    log.addEventListener('scroll', () => { if (isAtBottom(log)) hide(pillFor(id)); });
    pillFor(id).addEventListener('click', () => scrollToLatest(log));
    if ('IntersectionObserver' in window) {
      new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting) && unread > 0) {
          unread = 0;
          renderUnread();
        }
      }).observe(log);
    }
  });
  $('chat-jump').addEventListener('click', () => {
    const log = ['player-chat-log', 'spec-chat-log'].map($).find((el) => el.offsetParent !== null);
    if (!log) return;
    log.scrollIntoView({ behavior: 'smooth', block: 'center' });
    scrollToLatest(log);
    unread = 0;
    renderUnread();
    // Not focusing the input: on a phone that opens the keyboard over the game.
  });
}
