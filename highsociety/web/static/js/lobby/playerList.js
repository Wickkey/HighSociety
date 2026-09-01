// The waiting-room roster (before a game starts) -- a real per-seat grid
// (see JOIN_GAME_REWORK.MD), replacing the old plain-text "Seats filled:
// 3/5 (Alice, Bob, Marble bot 🤖)" line. Host-only inline controls here
// add a bot to an open seat or remove any seat (bot or human) -- see
// canManageSeats and web_server.py's _is_room_host for the exact same
// "no known host means open to anyone" fallback on both sides.
//
// Circular with network/messages.js (which imports startWaitingRoomPolling
// from here) and ui/modals.js (confirmDialog, which itself imports from
// lobby.js, which imports renderLobby from here) -- safe, same reasoning
// as lobby.js's own note: everything here is read inside a function body,
// never at this module's own top-level evaluation.
import { $, hide, show, showError, showScreen } from '../utils/dom.js';
import { fetchJSON, currentRoomCode, applyJoinIdentityDefaults } from './lobby.js';
import { takePendingIdentifyError } from '../network/messages.js';
import { loadProfile } from '../auth/profile.js';
import { confirmDialog } from '../ui/modals.js';
import { escapeHtml } from '../utils/formatting.js';

const BOT_TIERS = [
  { type: 'easy', label: 'Easy' },
  { type: 'medium', label: 'Medium' },
  { type: 'hard', label: 'Hard' },
];

// Which open seat's "add a bot" popover is showing, by seat index, or
// null -- purely local UI state, deliberately NOT part of the rendered-html
// diff below, so a poll tick that doesn't actually change the roster
// leaves an open popover alone instead of yanking it shut every 1.5s.
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
    const pickerOpen = openPickerSeatIndex === index;
    const options = BOT_TIERS.map(
      (t) => `<button type="button" class="lobby-bot-option" data-action="add-bot" data-seat-index="${index}" data-bot-type="${t.type}">${t.label}</button>`
    ).join('');
    return `
      <div class="lobby-seat open${pickerOpen ? ' picker-open' : ''}">
        <button type="button" class="lobby-seat-add-btn" aria-label="Add a bot" data-action="toggle-picker" data-seat-index="${index}">+</button>
        <div class="lobby-seat-name">Open</div>
        <div class="lobby-bot-picker"${pickerOpen ? '' : ' hidden'}>${options}</div>
      </div>`;
  }
  const safeName = escapeHtml(seat.name);
  const avatarContent = seat.is_bot ? '🤖' : escapeHtml(seat.name.charAt(0).toUpperCase());
  const nameHtml = seat.is_bot ? `${safeName}<span>Bot</span>` : safeName;
  const removeBtn = canManage
    ? `<button type="button" class="lobby-seat-remove" aria-label="Remove ${safeName}" data-action="remove-seat" data-username="${escapeHtml(seat.username)}" data-is-bot="${seat.is_bot}" data-name="${safeName}">×</button>`
    : '';
  return `
    <div class="lobby-seat">
      ${removeBtn}
      <div class="lobby-seat-avatar${seat.is_bot ? '' : ' is-human'}">${avatarContent}</div>
      <div class="lobby-seat-name">${nameHtml}</div>
    </div>`;
}

function renderSeatsGrid(status) {
  lastStatus = status;
  hide($('lobby-status'));
  show($('lobby-seats-wrap'));
  $('lobby-seats-label').textContent = `Seats filled: ${status.joined.length}/${status.seats}`;
  const canManage = canManageSeats(status);
  const seats = Array.from({ length: status.seats }, (_, i) => status.joined[i] || null);
  const html = seats.map((seat, i) => seatTileHtml(seat, i, canManage)).join('');
  const container = $('lobby-seats');
  if (container.dataset.renderedHtml === html) return; // no real change -- leave any open popover alone
  container.innerHTML = html;
  container.dataset.renderedHtml = html;
}

export function renderLobby(status) {
  showScreen('screen-join');
  $('join-form').classList.remove('hidden');
  $('join-waiting').classList.add('hidden');
  hide($('lobby-seats-error')); // a stale error from a previous room must never bleed into this one
  // A fresh entry point (not the ongoing poll) -- reset local seat-grid UI
  // state so a previous room's open bot-picker or diff cache can never
  // bleed into this one.
  openPickerSeatIndex = null;
  $('lobby-seats').dataset.renderedHtml = '';
  applyJoinIdentityDefaults();
  const visibilityNote = status.visibility === 'private' ? ' (private, share this code with friends)' : ' (public)';
  $('room-code-text').textContent = `Room code: ${status.room_code}${visibilityNote}`;
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
    const confirmed = await confirmDialog(
      `This removes ${name} from the game right away. They'll be notified and sent back to the home screen.`,
      'Remove player'
    );
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

// Single delegated listener, wired once at boot (see app.js) -- the grid's
// own innerHTML is fully replaced on every real change (renderSeatsGrid),
// so binding to individual buttons after each render would mean rebinding
// constantly; delegating to the never-replaced container avoids that.
export function initLobbySeatGrid() {
  const container = $('lobby-seats');
  container.addEventListener('click', (e) => {
    const target = e.target.closest('[data-action]');
    if (!target) return;
    // Never let this bubble to the document-level click-away listener
    // below -- relying on it to correctly no-op via e.target.closest on a
    // node that renderSeatsGridForced may have just detached (toggling the
    // picker replaces this container's whole innerHTML synchronously,
    // before bubbling reaches document) is fragile; just stop it here.
    e.stopPropagation();
    const action = target.dataset.action;
    if (action === 'toggle-picker') {
      const index = Number(target.dataset.seatIndex);
      openPickerSeatIndex = openPickerSeatIndex === index ? null : index;
      if (lastStatus) renderSeatsGridForced(lastStatus);
    } else if (action === 'add-bot') {
      addBot(target.dataset.botType);
    } else if (action === 'remove-seat') {
      removeSeat(target.dataset.username, target.dataset.isBot === 'true', target.dataset.name);
    }
  });
  // Clicking anywhere outside an open seat's own picker closes it --
  // same pattern as the card-info-popover elsewhere in this app.
  document.addEventListener('click', (e) => {
    if (openPickerSeatIndex === null) return;
    if (e.target.closest('.lobby-seat.open')) return;
    openPickerSeatIndex = null;
    if (lastStatus) renderSeatsGridForced(lastStatus);
  });
}

// renderSeatsGrid skips its own re-render when the html signature hasn't
// changed (see its own comment) -- exactly what a purely-local toggle like
// opening/closing the bot picker needs to bypass, since the roster itself
// hasn't changed at all.
function renderSeatsGridForced(status) {
  $('lobby-seats').dataset.renderedHtml = '';
  renderSeatsGrid(status);
}
