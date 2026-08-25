// Home screen, host/join forms, room browsing, spectate-join, and the
// status-polling loop that drives which screen is shown for the current
// room. Owns currentRoomCode/lastStatus/hasResigned as getter/setter pairs
// (not raw exported `let`s) since they're reassigned from several modules --
// an explicit function makes every write site grep-able.
//
// This module sits at the center of the app's circular-import web: nearly
// every feature module needs something from here (currentRoomCode,
// fetchJSON, applyJoinIdentityDefaults, ...), and this file needs things
// back from most of them. Every import below that closes a cycle is safe
// for the same reason documented in gameState.js/messages.js: everything
// is read inside a function body, never at this module's own top-level
// evaluation, so load order never matters.
import { $, hide, show, showError, showScreen } from '../utils/dom.js';
import { ensureProfileSet, loadProfile, renderProfileChip, saveProfile, setSessionStatus } from '../auth/profile.js';
import { game, resetGameState, seedOpponents, applyRoomDisplaySettings } from '../game/gameState.js';
import { renderOpponents } from '../game/gameRenderer.js';
import { ws, closeSocket, attemptReconnect, connectPlayerSocket, connectSpectatorSocket } from '../network/websocket.js';
import { setPendingJoin, setPendingSpectate } from '../network/messages.js';
import { confirmDialog } from '../ui/modals.js';
import { renderFinished } from './rematch.js';
import { renderLobby } from './playerList.js';

export async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

// Which room this browser tab is currently looking at — null means "no room
// yet, show the home screen" (public rooms list + host-a-game form). Kept
// in the URL (via history.replaceState) so a refresh or a shared link lands
// back in the same room instead of the home screen.
let currentRoomCodeValue = null;
export function currentRoomCode() { return currentRoomCodeValue; }
export function setCurrentRoomCode(v) { currentRoomCodeValue = v; }
export function clearCurrentRoomCode() { currentRoomCodeValue = null; }

let lastStatusValue = null;
export function lastStatus() { return lastStatusValue; }
export function setLastStatus(v) { lastStatusValue = v; }

let roomsPollTimer = null;

// Reconnection: if a refresh/dropped connection happens mid-game, the
// server keeps your seat (see web_server.py's rejoin-token handling) —
// this is what lets the browser find its way back to it. Stored per-room
// (not just "the" token) so multiple rooms across tabs/history don't clash.
let reconnectAttempted = false;
export function markReconnectAttempted() { reconnectAttempted = true; }

// Set the moment the user clicks Resign — the client already knows this seat
// is gone for good, so renderForStatus() should never try attemptReconnect()
// for it.
let hasResigned = false;
export function setHasResigned(v) { hasResigned = v; }

// Whether the join/spectate screens' "not you?" link has been clicked since
// the last time we landed on a fresh room — guards applyJoinIdentityDefaults
// (called on every lobby status poll, not just once) from stomping over
// someone's in-progress edit to their own name every 1.5s. Reset wherever a
// genuinely new room is entered.
let joinIdentityOverridden = false;

export function rejoinStorageKey(roomCode) {
  return `hs_rejoin_${roomCode}`;
}
export function saveRejoinInfo(roomCode, token, username, name) {
  localStorage.setItem(rejoinStorageKey(roomCode), JSON.stringify({ token, username, name }));
}
export function loadRejoinInfo(roomCode) {
  const raw = roomCode && localStorage.getItem(rejoinStorageKey(roomCode));
  if (!raw) return null;
  try {
    const info = JSON.parse(raw);
    return info && info.token && info.username ? info : null;
  } catch (e) {
    return null;
  }
}
export function clearRejoinInfo(roomCode) {
  if (roomCode) localStorage.removeItem(rejoinStorageKey(roomCode));
}

// Puts `room` in both the URL (so a refresh/shared link returns to the same
// room) and currentRoomCode, which every subsequent API/WS call reads from.
// Validates the code first (rather than just switching screens and letting
// the generic !status.exists path bounce back) so a mistyped room code
// gets a clear error right on the home screen instead of silently doing
// nothing.
export async function enterRoom(roomCode, event) {
  if (ensureProfileSet()) return;
  let status;
  try {
    status = await fetchJSON(`/api/status?room=${encodeURIComponent(roomCode)}`);
  } catch (e) {
    showError($('host-error'), 'Could not reach the server. Try again.');
    return;
  }
  if (!status.exists) {
    showError($('host-error'), `No game found with room code "${roomCode}".`);
    return;
  }
  joinIdentityOverridden = false; // a genuinely new room — start from the saved profile again
  currentRoomCodeValue = roomCode;
  history.replaceState(null, '', `?room=${encodeURIComponent(roomCode)}`);
  stopRoomsPolling();
  lastStatusValue = status;
  renderForStatus(status);
  startPolling();
}

// The home screen ("screen-host-setup") is a picker of three tiles (Join /
// Host / Rules) rather than dumping all three panels on screen at once —
// clicking a tile swaps to that panel with a "← Back" link back to the
// tiles. Always reset to the tile picker whenever the home screen itself is
// (re-)shown (see leaveToHome and the initial-load boot path), so returning
// home never leaves a stale panel expanded from a previous visit.
const HOME_TILE_TARGETS = ['join', 'host', 'rules'];
export function showHomeTile(target) {
  hide($('home-tiles'));
  HOME_TILE_TARGETS.forEach((t) => $(`home-panel-${t}`).classList.toggle('hidden', t !== target));
}
export function showHomeTiles() {
  show($('home-tiles'));
  HOME_TILE_TARGETS.forEach((t) => hide($(`home-panel-${t}`)));
}

// clearRejoin is false only for the "clicked the High Society title mid-game"
// path (see onHomeLinkClick) — that's meant to behave like closing the tab,
// which stays reconnectable, not like clicking "Return to Home" after the
// game's already over, which has nothing left to reconnect to.
export function leaveToHome(clearRejoin = true) {
  if (clearRejoin) clearRejoinInfo(currentRoomCodeValue);
  currentRoomCodeValue = null;
  reconnectAttempted = false;
  hasResigned = false;
  joinIdentityOverridden = false;
  history.replaceState(null, '', location.pathname);
  stopPolling();
  showScreen('screen-host-setup');
  showHomeTiles();
  renderProfileChip();
  startRoomsPolling();
}

// True once an actual seat is in play (not spectating, not still in the
// lobby, not already looking at results) — the one condition under which
// leaving should warn first, shared by the tab-close warning and the
// "High Society" home-link click.
export function isActivelyPlayingLiveGame() {
  const gameIsOver = !$('screen-finished').classList.contains('hidden');
  return !!(ws && ws.readyState === WebSocket.OPEN && game && game.round > 0 && !gameIsOver);
}

// The "High Society" wordmark doubles as a home link (most sites' logos
// do) — most of the time that's a free action, but mid-game it needs the
// same confirmation a browser's own "are you sure you want to leave"
// would give, and it closes the socket itself first rather than leaving it
// dangling while the UI has already moved on. Closing (rather than
// resigning) is deliberate: this should behave exactly like closing the
// tab would — recoverable via the room's own reconnect flow — not like
// clicking Resign, which is permanent.
// Returns whether it actually left (false only if a mid-game confirm was
// shown and declined) -- callers that need to chain a further navigation
// afterward (see navigateFromSidebar) can check this instead of assuming
// the leave always happened.
export async function onHomeLinkClick() {
  const midGame = isActivelyPlayingLiveGame();
  if (midGame) {
    const ok = await confirmDialog('Leave the game? You can rejoin later.', 'Leave');
    if (!ok) return false;
  }
  closeSocket();
  leaveToHome(!midGame);
  return true;
}

// Sidebar items are reachable from any screen, some of which (an active
// lobby wait, a live game) hold an open connection -- route through the
// same safe "leave" path the header title/lobby back-button already use
// (confirms first if actually mid-game) before jumping to the target,
// rather than yanking the screen out from under an open socket.
export async function navigateFromSidebar(afterHome) {
  const left = await onHomeLinkClick();
  if (left) afterHome();
}

export function startRoomsPolling() {
  if (roomsPollTimer) return;
  refreshRoomsList();
  roomsPollTimer = setInterval(refreshRoomsList, 2000);
}
export function stopRoomsPolling() {
  if (roomsPollTimer) { clearInterval(roomsPollTimer); roomsPollTimer = null; }
}

export async function refreshRoomsList() {
  let data;
  try {
    data = await fetchJSON('/api/rooms');
  } catch (e) {
    return; // transient network hiccup — next poll retries
  }
  renderRoomsList(data.rooms || []);
}

function renderRoomsList(rooms) {
  updateJoinTileLiveBadge(rooms.length);
  const container = $('public-rooms-list');
  if (!rooms.length) {
    container.innerHTML = '<p class="muted">No public games right now.</p>';
    return;
  }
  container.innerHTML = '';
  rooms.forEach((r) => {
    const row = document.createElement('div');
    row.className = 'room-row';
    const label = document.createElement('span');
    label.textContent = `Room ${r.room_code} (${r.joined}/${r.seats} seats filled)`;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'secondary';
    btn.textContent = 'Join';
    btn.addEventListener('click', (event) => enterRoom(r.room_code, event));
    row.appendChild(label);
    row.appendChild(btn);
    container.appendChild(row);
  });
}

// Surfaces live public-game activity right on the home screen's "Join a
// Game" tile (a small corner badge + swapped subtitle) instead of a
// separate banner element -- reuses the same /api/rooms poll that already
// drives the full list inside the tile's own panel (see refreshRoomsList),
// so this is purely a read of data already being fetched, not a new source.
const HOME_TILE_JOIN_SUB_DEFAULT = 'Public games or a room code';
function updateJoinTileLiveBadge(count) {
  const badge = $('home-tile-live-badge');
  const sub = $('home-tile-join-sub');
  if (count > 0) {
    badge.textContent = `${count} live`;
    show(badge);
    sub.textContent = `${count} public game${count === 1 ? '' : 's'} right now`;
    sub.classList.add('has-live');
  } else {
    hide(badge);
    sub.textContent = HOME_TILE_JOIN_SUB_DEFAULT;
    sub.classList.remove('has-live');
  }
}

export async function refreshStatus() {
  if (!currentRoomCodeValue) return;
  let status;
  try {
    status = await fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCodeValue)}`);
  } catch (e) {
    return; // transient network hiccup — next poll (or manual reload) retries
  }
  lastStatusValue = status;
  renderForStatus(status);
}

export function renderForStatus(status) {
  // The user is filling in the "watch as spectator" form — they haven't
  // opened a WebSocket yet (that only happens once they click "Watch"), so
  // the `ws` guards below don't cover this screen. Without this, a status
  // poll firing mid-fill (every 1.5s while the room is in "lobby") yanks
  // them back to the player-join screen before they can finish, making it
  // look like spectating before the lobby fills is simply impossible.
  if (!$('screen-spectate-join').classList.contains('hidden')) return;

  if (!status.exists) {
    // The room we were looking at is gone — either a bad/stale room code, or
    // the background reaper cleaned it up (see web_server.py's
    // _reap_stale_rooms). Nothing left to show for it; back to the home screen.
    leaveToHome();
    return;
  }
  if (status.state === 'finished') {
    stopPolling();
    renderFinished(status);
    return;
  }
  if (status.state === 'lobby') {
    if (ws) return; // mid-join; the join flow owns the screen until it resolves
    renderLobby(status);
    startPolling();
    return;
  }
  // starting / in_progress
  if (!ws) {
    if (!hasResigned && !reconnectAttempted) {
      reconnectAttempted = true;
      if (attemptReconnect()) {
        stopPolling();
        return;
      }
    }
    stopPolling();
    showScreen('screen-join');
    $('join-form').classList.add('hidden');
    $('join-waiting').classList.add('hidden');
    $('lobby-status').textContent = hasResigned
      ? "You resigned from this game. You can watch as a spectator."
      : 'A game is already in progress. You can watch as a spectator.';
  }
}

let statusPollTimer = null;
export function startPolling() {
  if (statusPollTimer) return;
  statusPollTimer = setInterval(refreshStatus, 1500);
}
export function stopPolling() {
  if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer = null; }
}

// Pre-fills the join screen's username/name fields from the saved profile
// and collapses them behind a "Joining as X — not you?" line, so returning
// to a room (or joining a new one) never re-asks for something already on
// file. Safe to call repeatedly (playerList.js's renderLobby calls this on
// every status poll) — a no-op past the first call unless the profile
// itself changes, and never overwrites an in-progress "not you?" edit (see
// joinIdentityOverridden).
export function applyJoinIdentityDefaults() {
  if (joinIdentityOverridden) return;
  const profile = loadProfile();
  if (profile) {
    $('join-username').value = profile.username;
    $('join-as-name').textContent = profile.username;
    show($('join-as-label'));
    hide($('join-identity-fields'));
  } else {
    hide($('join-as-label'));
    show($('join-identity-fields'));
  }
}

export function onChangeJoinIdentity() {
  joinIdentityOverridden = true;
  hide($('join-as-label'));
  show($('join-identity-fields'));
}

// Spectating has no recurring-poll render path the way the join screen
// does (screen-spectate-join is only ever shown once per click of "Watch as
// a spectator instead"), so this doesn't need joinIdentityOverridden's
// re-render guard — it just runs once, right when that screen is opened.
export function applySpectateIdentityDefaults() {
  const profile = loadProfile();
  if (profile) {
    $('spectate-username').value = profile.username;
    $('spectate-as-name').textContent = profile.username;
    show($('spectate-as-label'));
    hide($('spectate-identity-fields'));
  } else {
    hide($('spectate-as-label'));
    show($('spectate-identity-fields'));
  }
}

export function onChangeSpectateIdentity() {
  hide($('spectate-as-label'));
  show($('spectate-identity-fields'));
}

export async function onCreateGame() {
  if (ensureProfileSet()) return;
  hide($('host-error'));
  const seats = parseInt($('host-seats').value, 10);
  const counts = {
    easy: parseInt($('bot-easy').value || '0', 10),
    medium: parseInt($('bot-medium').value || '0', 10),
    hard: parseInt($('bot-hard').value || '0', 10),
  };
  const botMix = [];
  for (const [type, n] of Object.entries(counts)) for (let i = 0; i < n; i += 1) botMix.push(type);

  const turnTimeRaw = $('host-turn-time').value;
  const seedRaw = $('host-seed').value;
  const profile = loadProfile();
  const body = {
    seats,
    bot_mix: botMix,
    bot_think_time: parseFloat($('host-think-time').value || '1.5'),
    visibility: $('host-visibility-private').checked ? 'private' : 'public',
    turn_time_limit: turnTimeRaw ? parseFloat(turnTimeRaw) : null,
    reveal_cards: $('host-reveal-cards').checked,
    show_logs: $('host-show-logs').checked,
    // Purely informational (see game_history.py's games.host_player_id) --
    // ensureProfileSet() above already guarantees a profile exists here.
    host_username: profile ? profile.username : null,
  };
  if (seedRaw) body.seed = parseInt(seedRaw, 10);
  try {
    const status = await fetchJSON('/api/create_game', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    stopRoomsPolling();
    joinIdentityOverridden = false; // a genuinely new room — start from the saved profile again
    currentRoomCodeValue = status.room_code;
    history.replaceState(null, '', `?room=${encodeURIComponent(status.room_code)}`);
    lastStatusValue = status;
    renderLobby(status);
    startPolling();
  } catch (e) {
    showError($('host-error'), e.message);
  }
}

export function onJoinByCode(event) {
  hide($('host-error'));
  const code = $('join-room-code').value.trim().toUpperCase();
  if (!code) { showError($('host-error'), 'Enter a room code.'); return; }
  enterRoom(code, event);
}

// Copies the full joinable URL (not just the bare code) -- pasted into a
// chat app it becomes a one-tap link straight to this room's join screen,
// versus a bare code which still makes the recipient find the site
// themselves and type it in. The code itself is still visible as plain
// text right next to this button for anyone who'd rather read it aloud.
// Reads from #room-link-input (playerList.js's renderLobby already fills
// it in) rather than rebuilding the URL again here, so there's exactly one
// place that knows how to construct it.
export async function onCopyRoomLink() {
  const url = $('room-link-input').value;
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
  } catch (e) {
    return; // clipboard permission denied/unavailable -- silently no-op, nothing else useful to do
  }
  const btn = $('btn-copy-room-link');
  btn.classList.add('copied');
  btn.title = 'Copied!';
  clearTimeout(onCopyRoomLink._resetTimer);
  onCopyRoomLink._resetTimer = setTimeout(() => {
    btn.classList.remove('copied');
    btn.title = 'Copy invite link';
  }, 1500);
}

export function onJoin() {
  hide($('join-error'));
  const username = $('join-username').value.trim();
  if (!username) { showError($('join-error'), 'Username is required.'); return; }
  setPendingJoin({ username, name: username });
  saveProfile(username, username); // this device's identity going forward — see loadProfile
  stopPolling();
  resetGameState(username, lastStatusValue);
  if (lastStatusValue) seedOpponents(lastStatusValue, username);
  connectPlayerSocket();
}

export function onSpectateJoin() {
  hide($('spectate-error'));
  const username = $('spectate-username').value.trim();
  if (!username) { showError($('spectate-error'), 'Username is required.'); return; }
  setPendingSpectate({ username, name: username });
  saveProfile(username, username); // this device's identity going forward — see loadProfile
  stopPolling();
  resetGameState(null, lastStatusValue);
  fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCodeValue)}`)
    .then((status) => {
      seedOpponents(status, null);
      game.revealCards = status.reveal_cards !== false;
      game.showLogs = status.show_logs !== false;
      applyRoomDisplaySettings();
    }).catch(() => {});
  connectSpectatorSocket();
  showScreen('screen-spectate');
  setSessionStatus('spectating');
}
