// Player/spectator chat. Three parts, none of which covers anything you need
// during play:
//   - the chat bar, a slim strip fixed to the bottom of the table,
//   - bubbles that appear briefly beside the auction card and fade (the only
//     space on the table that's always empty),
//   - the full log, opened on demand from the bar. Every message stays there.
import { $, hide, show } from '../utils/dom.js';
import { sendIfOpen } from '../network/websocket.js';
import { nameOf } from '../game/gameState.js';

// How close to the bottom still counts as "reading the latest".
const AT_BOTTOM_PX = 40;

// Enough distinct names that a table of five rarely repeats one, all dark
// enough to read on the light bubble background.
const SENDER_COLORS = ['#8f6a22', '#1f7a4d', '#a3302a', '#2d5f8b', '#6f4a8e', '#a0522d', '#2f7f7a'];

// A bubble stays long enough to read, without lingering over a long message.
const BUBBLE_MIN_MS = 3500;
const BUBBLE_MAX_MS = 7000;
const BUBBLE_MS_PER_CHAR = 45;
const BUBBLE_FADE_MS = 200;
const BUBBLES_PER_SIDE = 2;

// Below this width the open log can sit over the money cards. See
// closeChatLogForYourTurn.
const LOG_COVERS_MOVES = '(max-width: 1199px)';

const LOG_IDS = ['player-chat-log', 'spec-chat-log'];
const INPUT_FOR_LOG = { 'player-chat-log': 'player-chat-input', 'spec-chat-log': 'spec-chat-input' };

function senderColor(username) {
  let hash = 0;
  for (let i = 0; i < username.length; i += 1) hash = (hash * 31 + username.charCodeAt(i)) % 1009;
  return SENDER_COLORS[hash % SENDER_COLORS.length];
}

// logId -> the sender of the last bubble in that log, so a run of messages from
// one person only names them once.
const lastSender = {};
let unread = 0;
let peekTimer = null;

function isAtBottom(log) {
  return log.scrollHeight - log.scrollTop - log.clientHeight < AT_BOTTOM_PX;
}

function pillFor(logId) {
  return document.querySelector(`[data-chat-pill="${logId}"]`);
}

function panelFor(logId) {
  return $(logId.replace('-log', '-panel'));
}

function isLogOpen(logId) {
  return !panelFor(logId).classList.contains('hidden');
}

function renderUnread() {
  document.querySelectorAll('[data-chat-unread]').forEach((badge) => {
    badge.textContent = unread > 99 ? '99+' : String(unread);
    badge.classList.toggle('hidden', unread === 0);
  });
}

function scrollToLatest(log) {
  log.scrollTop = log.scrollHeight;
  hide(pillFor(log.id));
}

function setLogOpen(logId, open) {
  panelFor(logId).classList.toggle('hidden', !open);
  $(`btn-${logId}`).setAttribute('aria-expanded', String(open));
  if (open) {
    scrollToLatest($(logId));
    unread = 0;
    renderUnread();
    // Not focusing the input: on a phone that opens the keyboard over the game.
  }
}

// Where the open log would sit over the move panel, it closes when a move is
// needed: the clock is running and the bid buttons have to be reachable. On a
// wide screen it sits beside the move panel and stays open.
export function closeChatLogForYourTurn() {
  if (window.matchMedia(LOG_COVERS_MOVES).matches) setLogOpen('player-chat-log', false);
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

function restingPlaceholder(inputId) {
  if (inputId === 'spec-chat-input') {
    return $('spec-chat-target-toggle').checked ? 'Message spectators only…' : 'Message everyone…';
  }
  return 'Say something…';
}

// Scrolled down on a phone, the auction card and its bubbles are off screen,
// so the newest message shows in the chat box's own placeholder instead. That
// space is already reserved, so this can't cover anything either.
function peekInChatBar(logId, text, durationMs) {
  const stage = $(`${logId.split('-')[0]}-table-talk-others`).getBoundingClientRect();
  if (stage.bottom > 0 && stage.top < window.innerHeight) return;
  const input = $(INPUT_FOR_LOG[logId]);
  input.placeholder = text;
  input.classList.add('peeking');
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => {
    input.placeholder = restingPlaceholder(input.id);
    input.classList.remove('peeking');
  }, durationMs);
}

// The phone table (see game.css): no room beside the card, so a message comes
// out of the speaker's own tile as a speech bubble instead.
const PHONE_TABLE = '(max-width: 600px)';

// Per log: the bubble layer, the tiles a speaker can be found in, and what
// to point at when they aren't at the table (a watcher).
const PHONE_TALK = {
  'player-chat-log': { layer: 'phone-talk', tiles: '#opponents-list', fallback: '#screen-game .opponents-panel' },
  'spec-chat-log': { layer: 'spec-phone-talk', tiles: '#spec-players-list', fallback: '#spectate-live-layout .side-col .panel' },
};

function onPhoneTable(logId) {
  return logId in PHONE_TALK && window.matchMedia(PHONE_TABLE).matches;
}

// Your own message on a phone: shown for a moment in the chat box itself,
// which covers nothing (a bubble down there would sit on your bid buttons).
function echoInChatBar(logId, text, durationMs) {
  const input = $(INPUT_FOR_LOG[logId]);
  input.placeholder = `You: ${text}`;
  input.classList.add('peeking');
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => {
    input.placeholder = restingPlaceholder(input.id);
    input.classList.remove('peeking');
  }, durationMs);
}

function showSpeechBubble(logId, { username, name, text, big, tag }, durationMs) {
  const where = PHONE_TALK[logId];
  const layer = $(where.layer);
  const key = username || name || '';
  // One bubble per speaker: a new message replaces their last one.
  [...layer.children].filter((b) => b.dataset.speaker === key).forEach((b) => b.remove());
  const tile = username
    ? document.querySelector(`${where.tiles} .opponent-row[data-username="${CSS.escape(username)}"]`)
    : null;
  const bubble = document.createElement('div');
  bubble.className = `speech-bubble${big ? ' big' : ''}`;
  bubble.dataset.speaker = key;
  // Coming out of their tile says who it is; anyone without one (a
  // spectator) gets their name on it.
  if (!tile && name) {
    const who = document.createElement('span');
    who.className = 'speech-bubble-name';
    who.textContent = tag ? `${name} · ${tag}` : name;
    bubble.appendChild(who);
  }
  const body = document.createElement('span');
  body.className = 'speech-bubble-text';
  body.textContent = text; // always text, never markup: this came from another player
  bubble.appendChild(body);
  layer.appendChild(bubble);

  const area = layer.getBoundingClientRect();
  const anchor = (tile || document.querySelector(where.fallback)).getBoundingClientRect();
  const width = bubble.offsetWidth;
  const centre = anchor.left + anchor.width / 2 - area.left;
  const left = Math.max(8, Math.min(area.width - width - 8, centre - width / 2));
  bubble.style.left = `${left}px`;
  bubble.style.top = `${anchor.bottom - area.top + 8}px`;
  bubble.style.setProperty('--tail-x', `${Math.max(14, Math.min(width - 14, centre - left))}px`);
  requestAnimationFrame(() => bubble.classList.add('show'));
  setTimeout(() => {
    bubble.classList.remove('show');
    setTimeout(() => bubble.remove(), BUBBLE_FADE_MS);
  }, durationMs);
}

// `name` is who said it (null for your own bubble), `big` for a lone reaction.
// `username` lets the phone table find the speaker's tile.
function showTableBubble(logId, { name, text, mine, big = false, tag = null, username = null }) {
  const duration = Math.min(BUBBLE_MAX_MS, BUBBLE_MIN_MS + text.length * BUBBLE_MS_PER_CHAR);
  if (onPhoneTable(logId)) {
    if (mine) echoInChatBar(logId, text, duration);
    else showSpeechBubble(logId, { username, name, text, big, tag }, duration);
    return;
  }
  const lane = $(`${logId.split('-')[0]}-table-talk-${mine ? 'mine' : 'others'}`);
  const bubble = document.createElement('div');
  bubble.className = `table-bubble${big ? ' big' : ''}`;
  if (name) {
    const who = document.createElement('span');
    who.className = 'table-bubble-name';
    who.textContent = tag ? `${name} · ${tag}` : name;
    bubble.appendChild(who);
  }
  const body = document.createElement('span');
  body.className = 'table-bubble-text';
  body.textContent = text; // always text, never markup: this came from another player
  bubble.appendChild(body);
  while (lane.children.length >= BUBBLES_PER_SIDE) lane.firstElementChild.remove();
  lane.appendChild(bubble);
  // Two long messages can overflow a phone's narrow lane. The newest wins; the
  // older one is still in the log.
  while (lane.children.length > 1 && lane.scrollHeight > lane.clientHeight) lane.firstElementChild.remove();
  requestAnimationFrame(() => bubble.classList.add('show'));

  setTimeout(() => {
    bubble.classList.remove('show');
    setTimeout(() => bubble.remove(), BUBBLE_FADE_MS);
  }, duration);
  if (!mine) peekInChatBar(logId, name ? `${name}: ${text}` : text, duration);
}

// A quick reaction: same bubble, just the emoji. Reactions are passing noise,
// so unlike messages they don't go in the log or count as unread.
export function showTableReaction(username, emoji, { mine, isSpectator }) {
  showTableBubble(isSpectator ? 'spec-chat-log' : 'player-chat-log', {
    name: mine ? null : nameOf(username),
    text: emoji,
    mine,
    big: true,
    username,
  });
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
  const tag = msg['to_user(s)'] === 'spectators' ? 'spectators only' : null;
  const text = messageBody(msg.prompt || '', from);
  appendBubble(logId, { from, text, mine: false, tag });
  showTableBubble(logId, { name: nameOf(from), text, mine: false, tag, username: from });
  if (!isLogOpen(logId)) {
    unread += 1;
    renderUnread();
  }
}

export function resetChat() {
  Object.keys(lastSender).forEach((id) => { delete lastSender[id]; });
  unread = 0;
  renderUnread();
  document.querySelectorAll('[data-chat-pill]').forEach(hide);
  document.querySelectorAll('.table-talk').forEach((lane) => { lane.innerHTML = ''; });
  LOG_IDS.forEach((id) => setLogOpen(id, false));
  closeReactionPicker();
}

function send(logId, inputId, payload, tag) {
  const input = $(inputId);
  const text = input.value.trim();
  if (!text) return;
  if (!sendIfOpen({ message_type: 'CHAT', prompt: text, ...payload })) {
    // Kept in the box so it isn't lost: send it again once reconnected.
    appendNote(logId, 'Not connected, so that message wasn\'t sent.');
    if (!isLogOpen(logId)) setLogOpen(logId, true); // the log is where that note is
    return;
  }
  // A CHAT message is never echoed back to its own sender over the wire (see
  // docs/PLAYING.md), so the sender's own bubble is added here.
  appendBubble(logId, { from: '__me__', text, mine: true, tag });
  showTableBubble(logId, { name: null, text, mine: true, tag });
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

// On a phone the reactions sit behind one button; wider screens show them in
// the bar and CSS hides this button.
function closeReactionPicker() {
  $('quick-reactions').classList.remove('open');
  $('btn-reaction-picker').setAttribute('aria-expanded', 'false');
}

function initReactionPicker() {
  const toggle = $('btn-reaction-picker');
  const reactions = $('quick-reactions');
  toggle.addEventListener('click', () => {
    const open = reactions.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(open));
  });
  reactions.addEventListener('click', closeReactionPicker);
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.reaction-picker')) closeReactionPicker();
  });
}

// An on-screen keyboard shrinks the visual viewport, not the layout one fixed
// elements are placed against, so the bar lifts itself to stay above it while
// you're typing.
function initKeyboardOffset() {
  const viewport = window.visualViewport;
  if (!viewport) return;
  const sync = () => {
    const typing = Object.values(INPUT_FOR_LOG).includes(document.activeElement && document.activeElement.id);
    const offset = typing ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0;
    document.documentElement.style.setProperty('--keyboard-offset', `${Math.round(offset)}px`);
  };
  viewport.addEventListener('resize', sync);
  viewport.addEventListener('scroll', sync);
  document.addEventListener('focusin', sync);
  document.addEventListener('focusout', sync);
}

// Wired once at startup from app.js.
export function initChat() {
  LOG_IDS.forEach((id) => {
    const log = $(id);
    log.addEventListener('scroll', () => { if (isAtBottom(log)) hide(pillFor(id)); });
    pillFor(id).addEventListener('click', () => scrollToLatest(log));
    panelFor(id).addEventListener('keydown', (e) => { if (e.key === 'Escape') setLogOpen(id, false); });
  });
  document.querySelectorAll('[data-chat-toggle]').forEach((button) => {
    const id = button.dataset.chatToggle;
    button.addEventListener('click', () => setLogOpen(id, !isLogOpen(id)));
  });
  $('spec-chat-target-toggle').addEventListener('change', () => {
    $('spec-chat-input').placeholder = restingPlaceholder('spec-chat-input');
  });
  initReactionPicker();
  initKeyboardOffset();
}
