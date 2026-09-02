// The waiting-room roster (before a game starts) -- a real per-seat grid
// (see JOIN_GAME_REWORK.MD), replacing the old plain-text "Seats filled:
// 3/5 (Alice, Bob, Marble bot 🤖)" line. Host-only inline controls here
// add a bot to an open seat (via one shared "Fill seat N with a bot?"
// panel below the grid, not a per-seat popover) or remove any seat (bot
// or human) -- see canManageSeats and web_server.py's _is_room_host for
// the exact same "no known host means open to anyone" fallback on both
// sides. buildSeatsHtml is also reused as-is (always read-only) for the
// spectate screen's own lobby-wait view further down this file, so both
// experiences genuinely look the same, not two near-copies.
//
// Circular with network/messages.js (which imports startWaitingRoomPolling
// from here), ui/modals.js (confirmDialog, which itself imports from
// lobby.js, which imports renderLobby from here), and game/gameEvents.js
// (which imports revealSpectateLiveLayout) -- safe, same reasoning as
// lobby.js's own note: everything here is read inside a function body,
// never at this module's own top-level evaluation.
import { $, hide, show, showError, showScreen } from '../utils/dom.js';
import { fetchJSON, currentRoomCode, applyJoinIdentityDefaults } from './lobby.js';
import { takePendingIdentifyError } from '../network/messages.js';
import { loadProfile } from '../auth/profile.js';
import { confirmDialog } from '../ui/modals.js';
import { escapeHtml } from '../utils/formatting.js';

// Distinct emoji + tinted background per bot, so a table of several bots
// doesn't read as one repeated grey 🤖 tile -- deterministically assigned
// from the bot's own (stable, unique-within-the-room) username via
// _hashString below, not random, so a given bot keeps the same look
// across every re-render/poll tick rather than visibly flickering colors.
// Colors are soft tints of hues already used elsewhere in this app's own
// palette (green/purple/teal/terracotta/tan), not the mockup's own
// unrelated named-bot-tier colors.
const BOT_AVATAR_STYLES = [
  { bg: 'rgba(47, 168, 79, 0.22)', emoji: '🐝' },
  { bg: 'rgba(133, 112, 191, 0.22)', emoji: '🎩' },
  { bg: 'rgba(199, 120, 63, 0.22)', emoji: '🔭' },
  { bg: 'rgba(70, 138, 150, 0.22)', emoji: '🍀' },
  { bg: 'rgba(140, 129, 113, 0.22)', emoji: '🐣' },
  { bg: 'rgba(180, 130, 170, 0.22)', emoji: '🦉' },
];
function _hashString(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function botAvatarStyle(username) {
  return BOT_AVATAR_STYLES[_hashString(username) % BOT_AVATAR_STYLES.length];
}

// Which open seat the shared "add a bot" panel below the grid is currently
// targeting, by seat index, or null -- purely local UI state, deliberately
// NOT part of the rendered-html diff below, so a poll tick that doesn't
// actually change the roster leaves an open panel alone instead of
// yanking it shut every 1.5s.
let openPickerSeatIndex = null;
// The last status this screen rendered -- lets a local-only UI change
// (opening/closing the bot picker) redraw immediately without waiting for
// the next poll tick.
let lastStatus = null;

function canManageSeats(status) {
  // No known host (an older client, or a matchmaking room -- see
  // GameRoom.host_username's own comment) means seat management stays
  // open to anyone, exactly matching web_server.py's _is_room_host.
  if (!status.host_username) return true;
  const profile = loadProfile();
  return !!profile && profile.username === status.host_username;
}

function seatTileHtml(seat, index, canManage) {
  if (!seat) {
    if (!canManage) {
      return '<div class="lobby-seat open"><div class="lobby-seat-name">Open</div></div>';
    }
    // Highlighted while this is the seat the shared picker panel below
    // (not a per-seat popover any more -- see #lobby-bot-picker-panel's
    // own comment) is currently targeting, so it's obvious at a glance
    // which seat "Fill this seat with a bot?" actually refers to.
    const selected = openPickerSeatIndex === index;
    return `
      <div class="lobby-seat open${selected ? ' selected' : ''}">
        <button type="button" class="lobby-seat-add-btn" aria-label="Add a bot" data-action="toggle-picker" data-seat-index="${index}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
        </button>
        <div class="lobby-seat-name">Open</div>
      </div>`;
  }
  const safeName = escapeHtml(seat.name);
  let avatarStyle = '';
  let avatarContent;
  if (seat.is_bot) {
    const style = botAvatarStyle(seat.username);
    avatarStyle = ` style="background: ${style.bg}"`;
    avatarContent = style.emoji;
  } else {
    avatarContent = escapeHtml(seat.name.charAt(0).toUpperCase());
  }
  const nameHtml = seat.is_bot ? `${safeName}<span>Bot</span>` : safeName;
  const removeBtn = canManage
    ? `<button type="button" class="lobby-seat-remove" aria-label="Remove ${safeName}" data-action="remove-seat" data-username="${escapeHtml(seat.username)}" data-is-bot="${seat.is_bot}" data-name="${safeName}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>`
    : '';
  return `
    <div class="lobby-seat">
      ${removeBtn}
      <div class="lobby-seat-avatar${seat.is_bot ? '' : ' is-human'}"${avatarStyle}>${avatarContent}</div>
      <div class="lobby-seat-name">${nameHtml}</div>
    </div>`;
}

// Pure HTML builder, shared by the join screen's own grid below and the
// spectate screen's read-only lobby-wait view (see spectateLobby.js) --
// the whole point of both showing "the same thing" (the actual literal
// ask: a spectator's wait experience should look consistent with a
// player's) is that they paint through this exact one function, not two
// parallel near-copies that could quietly drift apart later.
export function buildSeatsHtml(status, canManage) {
  const seats = Array.from({ length: status.seats }, (_, i) => status.joined[i] || null);
  return seats.map((seat, i) => seatTileHtml(seat, i, canManage)).join('');
}

// The one shared "add a bot" panel below the grid -- see its own
// index.html comment for why this replaced a per-seat floating popover.
// Plain "this seat" rather than naming a seat number: the targeted seat
// is already highlighted directly (see seatTileHtml's .selected class),
// so a number here would just be redundant, unrequested detail.
function renderBotPickerPanel(status) {
  const panel = $('lobby-bot-picker-panel');
  if (openPickerSeatIndex === null || !status || openPickerSeatIndex >= status.seats) {
    hide(panel);
    return;
  }
  show(panel);
}

function renderSeatsGrid(status) {
  lastStatus = status;
  hide($('lobby-status'));
  show($('lobby-seats-wrap'));
  $('lobby-seats-label').textContent = `Seats filled: ${status.joined.length}/${status.seats}`;
  const canManage = canManageSeats(status);
  const html = buildSeatsHtml(status, canManage);
  const container = $('lobby-seats');
  if (container.dataset.renderedHtml !== html) {
    container.innerHTML = html;
    container.dataset.renderedHtml = html;
  }
  renderBotPickerPanel(status); // not gated by the html diff above -- open/closed state can change with no roster change at all
}

// The room this screen was last freshly initialized for -- lets renderLobby
// tell "a genuinely new room" (reset everything, including the bot picker)
// apart from "the same room's own status poll firing again" (every 1.5s
// the whole time you're sitting on the pre-join form, via lobby.js's
// refreshStatus/renderForStatus -- startWaitingRoomPolling doesn't even
// exist yet at this point, you haven't joined). Before this, renderLobby
// unconditionally reset openPickerSeatIndex on every call, so opening the
// bot picker and then not immediately clicking a tier button lost the race
// against the very next poll tick -- a real reported bug ("the bot
// selector disappears immediately, I can't click it").
let lastRenderedLobbyRoomCode = null;

export function renderLobby(status) {
  showScreen('screen-join');
  $('join-form').classList.remove('hidden');
  $('join-waiting').classList.add('hidden');
  const isFreshRoom = lastRenderedLobbyRoomCode !== status.room_code;
  if (isFreshRoom) {
    lastRenderedLobbyRoomCode = status.room_code;
    hide($('lobby-seats-error')); // a stale error from a previous room must never bleed into this one
    // Reset local seat-grid UI state so a previous room's open bot-picker
    // or diff cache can never bleed into this one.
    openPickerSeatIndex = null;
    $('lobby-seats').dataset.renderedHtml = '';
  }
  applyJoinIdentityDefaults();
  $('room-code-value').textContent = status.room_code;
  const isPrivate = status.visibility === 'private';
  const tag = $('room-visibility-tag');
  tag.textContent = isPrivate ? 'Private game' : 'Public game';
  tag.classList.toggle('is-private', isPrivate);
  tag.title = isPrivate ? 'Only joinable with this room code' : 'Listed for anyone to join';
  show($('room-code-display'));
  $('room-link-input').value = `${location.origin}${location.pathname}?room=${encodeURIComponent(status.room_code)}`;
  show($('room-link-row'));
  renderSeatsGrid(status);
  const err = takePendingIdentifyError();
  if (err) showError($('join-error'), err);
}

// Separate from the main status poll (which onJoin() stops the moment you
// connect — see lobby.js's connectPlayerSocket) because "waiting in the
// lobby after joining" is a distinct phase: nothing about the shared game/
// opponents state has started yet, but you still want to see the seat
// grid update live as other people join/leave/get added/removed. Self-
// cancels once the room leaves "lobby" (game started), so it never runs
// for the rest of the game.
let waitingRoomPollTimer = null;

async function _tickWaitingRoomStatus() {
  let status;
  try {
    status = await fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode())}`);
  } catch (e) {
    return;
  }
  if (!status.exists || status.state !== 'lobby') {
    stopWaitingRoomPolling();
    return;
  }
  renderSeatsGrid(status);
}

export function startWaitingRoomPolling() {
  if (waitingRoomPollTimer) return;
  // Without this immediate call, setInterval's first tick is 1.5s out --
  // the seat grid would still show whatever it showed before you joined
  // sitting directly next to "You're in!" for that whole window, visibly
  // contradicting it. Matches lobby.js's startRoomsPolling same fix.
  _tickWaitingRoomStatus();
  waitingRoomPollTimer = setInterval(_tickWaitingRoomStatus, 1500);
}
export function stopWaitingRoomPolling() {
  if (waitingRoomPollTimer) { clearInterval(waitingRoomPollTimer); waitingRoomPollTimer = null; }
}

function seatGridError(message) {
  showError($('lobby-seats-error'), message);
}

async function addBot(botType) {
  openPickerSeatIndex = null;
  hide($('lobby-seats-error'));
  try {
    const status = await fetchJSON('/api/add_bot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        room: currentRoomCode(),
        bot_type: botType,
        requester_username: loadProfile() ? loadProfile().username : null,
      }),
    });
    // Paint the response's own fresh status immediately -- an action *you*
    // just took reflecting back up to 1.5s later (the next waiting-room
    // poll tick) reads as sluggish/broken in a way that someone *else's*
    // join, which has no faster signal available, doesn't. If this filled
    // the last seat the game itself starts server-side and this player's
    // already-open WebSocket takes over from here; nothing left for the
    // (self-cancelling) lobby grid to render either way.
    if (status.state === 'lobby') renderSeatsGrid(status);
  } catch (e) {
    seatGridError(e.message);
  }
}

async function removeSeat(username, isBot, name) {
  if (!isBot) {
    const confirmed = await confirmDialog(`Kick ${name}?`, 'Kick');
    if (!confirmed) return;
  }
  hide($('lobby-seats-error'));
  try {
    const status = await fetchJSON('/api/remove_seat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        room: currentRoomCode(),
        username,
        requester_username: loadProfile() ? loadProfile().username : null,
      }),
    });
    // Same immediate-paint reasoning as addBot above; a kicked human also
    // gets their own KICKED message over their own connection (see
    // network/messages.js), independent of this render.
    if (status.state === 'lobby') renderSeatsGrid(status);
  } catch (e) {
    seatGridError(e.message);
  }
}

// Single delegated listener on the whole wrap (grid + the shared bot-
// picker panel below it), wired once at boot (see app.js) -- the grid's
// own innerHTML is fully replaced on every real roster change
// (renderSeatsGrid), so binding to individual buttons after each render
// would mean rebinding constantly; delegating to the never-replaced
// wrapper avoids that, and covers the picker panel's own buttons too
// since they're a sibling of the grid, not inside it.
export function initLobbySeatGrid() {
  const wrap = $('lobby-seats-wrap');
  wrap.addEventListener('click', (e) => {
    const target = e.target.closest('[data-action]');
    if (!target) return;
    // Never let this bubble to the document-level click-away listener
    // below -- toggling the picker re-renders the grid synchronously
    // (different .selected state = different html, see seatTileHtml),
    // and relying on a click-away check to correctly no-op against a node
    // that render may have just detached is fragile; just stop it here.
    e.stopPropagation();
    const action = target.dataset.action;
    if (action === 'toggle-picker') {
      const index = Number(target.dataset.seatIndex);
      openPickerSeatIndex = openPickerSeatIndex === index ? null : index;
      if (lastStatus) renderSeatsGrid(lastStatus);
    } else if (action === 'add-bot') {
      addBot(target.dataset.botType);
    } else if (action === 'remove-seat') {
      removeSeat(target.dataset.username, target.dataset.isBot === 'true', target.dataset.name);
    }
  });
  // Clicking anywhere outside the picker panel closes it -- same pattern
  // as the card-info-popover elsewhere in this app. Every click that
  // should instead *change* openPickerSeatIndex (a "+", a tier button)
  // already stopped its own propagation above, so this only ever fires
  // for a genuine click-away.
  document.addEventListener('click', (e) => {
    if (openPickerSeatIndex === null) return;
    if (e.target.closest('#lobby-bot-picker-panel')) return;
    openPickerSeatIndex = null;
    if (lastStatus) renderSeatsGrid(lastStatus);
  });
}

// ---------------- spectating a room still in its lobby ----------------
// See index.html's own comment on #spectate-lobby-wait for the bug this
// fixes. Always read-only (a spectator never manages seats, regardless of
// whose account they're signed in as -- even the room's own host, watching
// their own game from a second tab, gets the plain view here), so this
// never needs anything like canManageSeats.
export function renderSpectateLobbyWait(status) {
  $('spectate-lobby-seats-label').textContent = `Seats filled: ${status.joined.length}/${status.seats}`;
  const html = buildSeatsHtml(status, false);
  const container = $('spectate-lobby-seats');
  if (container.dataset.renderedHtml === html) return;
  container.innerHTML = html;
  container.dataset.renderedHtml = html;
}

// Reveals the real live table and hides the lobby-wait view -- called the
// instant any real game message arrives (see gameEvents.js's
// ensureGameScreenVisible), which is exactly the same "the game is
// actually live now" signal a player's own screen already keys off of.
// A no-op once already showing, so calling this on every single message
// (not just the first) costs nothing.
export function revealSpectateLiveLayout() {
  stopSpectateLobbyPolling();
  hide($('spectate-lobby-wait'));
  show($('spectate-live-layout'));
}

let spectateLobbyPollTimer = null;

async function _tickSpectateLobbyStatus() {
  let status;
  try {
    status = await fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode())}`);
  } catch (e) {
    return;
  }
  if (!status.exists) return; // let the shared status poll's own !exists handling take it from here
  if (status.state !== 'lobby') { revealSpectateLiveLayout(); return; }
  renderSpectateLobbyWait(status);
}

export function startSpectateLobbyPolling() {
  if (spectateLobbyPollTimer) return;
  _tickSpectateLobbyStatus();
  spectateLobbyPollTimer = setInterval(_tickSpectateLobbyStatus, 1500);
}
export function stopSpectateLobbyPolling() {
  if (spectateLobbyPollTimer) { clearInterval(spectateLobbyPollTimer); spectateLobbyPollTimer = null; }
}

// Called once from onSpectateJoin with the room's status at connect time --
// decides which of the two views (lobby-wait vs. the real live table)
// this spectate session actually starts on.
export function showSpectateForStatus(status) {
  $('spectate-lobby-seats').dataset.renderedHtml = ''; // a previous spectate session's grid must never bleed into this one
  if (status && status.state === 'lobby') {
    hide($('spectate-live-layout'));
    show($('spectate-lobby-wait'));
    startSpectateLobbyPolling();
  } else {
    revealSpectateLiveLayout();
  }
}
