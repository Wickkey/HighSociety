// HighSociety web frontend — vanilla JS, no build step and no external
// dependencies. Talks the exact JSON message protocol described in
// BOT_API.md/network/protocol.py: this is "just another remote player",
// same as network_client.py, over a WebSocket instead of a raw socket.

const $ = (id) => document.getElementById(id);

function showScreen(id) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.add('hidden'));
  $(id).classList.remove('hidden');
}

function hide(el) { el.classList.add('hidden'); }
function show(el) { el.classList.remove('hidden'); }
function showError(el, text) { el.textContent = text; el.classList.remove('hidden'); }

// In-page confirm dialog (resign, leaving mid-game — see onResign/
// onHomeLinkClick) styled like every other panel in the app, replacing the
// browser's own native confirm() popup. Promise-based so a call site just
// reads `if (!(await confirmDialog(...))) return;`, same shape as the
// native confirm() it replaces. Only one can be open at a time in this app
// (both call sites are themselves mutually exclusive — you can't resign
// and click the home link in the same instant), so a single pending
// resolver is enough; no stacking/queueing needed.
let _confirmDialogResolve = null;
function confirmDialog(message, confirmLabel) {
  return new Promise((resolve) => {
    _confirmDialogResolve = resolve;
    $('confirm-modal-message').textContent = message;
    $('confirm-modal-confirm').textContent = confirmLabel;
    show($('confirm-modal'));
  });
}
function _resolveConfirmDialog(result) {
  hide($('confirm-modal'));
  const resolve = _confirmDialogResolve;
  _confirmDialogResolve = null;
  if (resolve) resolve(result);
}

// Usernames are entirely user-supplied and end up interpolated into a couple
// of innerHTML strings (final standings, the turn indicator) — escape them
// first so a username like "<img src=x onerror=...>" renders as inert text
// instead of executing in every other connected browser.
const _escapeHtmlEl = document.createElement('div');
function escapeHtml(text) {
  _escapeHtmlEl.textContent = text;
  return _escapeHtmlEl.innerHTML;
}

function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${path}`;
}

function setBadge(text) {
  const badge = $('connection-badge');
  $('connection-badge-text').textContent = text;
  badge.classList.remove('hidden');
  // A live room session's identity is already fixed — editing the *saved*
  // profile wouldn't change the current seat, so the chip stops being an
  // "edit" target for as long as this text describes a session (see
  // renderProfileChip, which re-enables it once back at the idle state).
  badge.classList.remove('editable');
  badge.classList.remove('needs-attention');
  closeProfilePopover();
}

// ---------------------------------------------------------------- cards --

// "You" instead of your own username in event toasts — reads more naturally
// when it's your own action being announced back to you. Spectators have no
// game.myUsername (it's null), so this is a no-op for them; they always see
// real names, which is correct since they aren't a player at the table.
function actorLabel(username) {
  return game && username === game.myUsername ? 'You' : username;
}

function describeCard(card) {
  const names = { Painting: `Painting (${card.value})`, PrestigeCard: 'Prestige Card (×2)',
    FauxPas: 'Faux Pas', Passe: 'Passe (−5)', Scandale: 'Scandale (½×, green)' };
  return names[card.type] || card.type;
}

// Color coding is deliberately just green-vs-not: Prestige and Scandale are
// the two actual "green cards" (see is_green / the green_card_limit rule),
// so only they get real green — every other card shares one neutral tone
// rather than each type having its own color, keeping green a meaningful
// signal instead of one hue among several.
function cardLabel(card) {
  switch (card.type) {
    case 'Painting': return { cls: 'neutral', text: String(card.value) };
    case 'PrestigeCard': return { cls: 'green', text: '×2' };
    case 'FauxPas': return { cls: 'neutral', text: 'Faux Pas' };
    case 'Passe': return { cls: 'neutral', text: '−5' };
    case 'Scandale': return { cls: 'green', text: '½×' };
    default: return { cls: '', text: card.type };
  }
}

function cardEl(card, big) {
  const { cls, text } = cardLabel(card);
  const div = document.createElement('div');
  div.className = `status-card ${cls}${big ? ' big' : ''}`;
  div.innerHTML = `<span class="value">${text}</span>${card.is_green ? '<span class="green-dot"></span>' : ''}`;
  if (card.description) div.title = card.description;
  return div;
}

function cardBackEl() {
  const div = document.createElement('div');
  div.className = 'card-back';
  div.title = 'Hidden — enable "Reveal cards" to see what this is';
  return div;
}

// ------------------------------------------------- transient event queue --
//
// Persistent game state (auction card, current bid, whose turn, player
// cards) lives in the `game` object and is re-rendered in place whenever it
// changes — no queueing, no big animations, it's just "what's true right
// now". Server events (RAISE/PASS/BUY/START_AUCTION/END_AUCTION/
// REVEAL_GREEN/DISGRACE_ASSIGNED) are a separate, transient concept: each is
// a brief announcement in a single toast slot centered over the auction
// card, queued one at a time so a burst of events (e.g. several bots acting
// quickly) never overlaps into an unreadable pile-up. The state itself is
// never gated on this queue — it always updates immediately.
const eventQueue = { game: [], spec: [] };
const eventQueueBusy = { game: false, spec: false };
const TOAST_DURATION_MS = 1500; // long enough to actually read before it clears
// "X bought Y for Z" / "X is stuck with Y" packs in more to actually read
// (who, what card, how much) than a routine bid/pass update — this was
// specifically called out as feeling rushed for someone new to the game,
// so it gets noticeably longer before the next auction's own toast can
// claim the slot.
const RESULT_TOAST_DURATION_MS = 3000;

function enqueueEvent(isSpectator, text, tone) {
  const key = isSpectator ? 'spec' : 'game';
  const duration = (tone === 'buy' || tone === 'disgrace') ? RESULT_TOAST_DURATION_MS : TOAST_DURATION_MS;
  eventQueue[key].push({ text, tone, duration });
  pumpEventQueue(key);
}

function pumpEventQueue(key) {
  if (eventQueueBusy[key] || eventQueue[key].length === 0) return;
  eventQueueBusy[key] = true;
  const { text, tone, duration } = eventQueue[key].shift();
  const toast = $(key === 'spec' ? 'spec-event-toast' : 'event-toast');
  toast.textContent = text;
  toast.className = `event-toast tone-${tone}`;
  requestAnimationFrame(() => toast.classList.add('show'));
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => {
      eventQueueBusy[key] = false;
      pumpEventQueue(key);
    }, 250); // let the fade-out clear before the next toast claims the slot
  }, duration);
}

function ordinal(n) {
  const rem10 = n % 10;
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  return `${n}${{ 1: 'st', 2: 'nd', 3: 'rd' }[rem10] || 'th'}`;
}

// The game-ending green card gets a dedicated, unmissable overlay rather
// than going through enqueueEvent/the toast queue — it's a one-off moment
// that shouldn't be interruptible or cut short by whatever narration would
// normally queue up next (and nothing does queue up after it anyway, since
// the game ends here). Self-cleans visually the instant showScreen() hides
// this screen for the finished screen, so no extra coordination is needed
// between this timer and the screen transition.
// Tracks when the final-green overlay last appeared, so the game_over
// handler below can hold off switching to the results screen until it's
// actually had time on screen — the game ending this way has nothing left
// to broadcast afterward (see gameplay.py's comment on this being
// deliberately unpaced), so game_over used to arrive right on its heels
// and cut the overlay off before a human could read it.
let finalGreenOverlayShownAt = null;

function showFinalGreenOverlay(isSpectator, count) {
  const overlay = $(isSpectator ? 'spec-final-green-overlay' : 'final-green-overlay');
  overlay.querySelector('.final-green-title').textContent = `${ordinal(count)} Green Card Revealed!`;
  overlay.classList.remove('show');
  void overlay.offsetWidth; // restart the entrance animation even on a rapid repeat
  overlay.classList.add('show');
  finalGreenOverlayShownAt = Date.now();
  clearTimeout(overlay._hideTimer);
  overlay._hideTimer = setTimeout(() => overlay.classList.remove('show'), 4000);
}

// The pre-game overlay is a quiet "Starting soon…" with a CSS-only animated
// ellipsis (see .game-start-dots in style.css) — no per-tick number to
// render, so unlike the old 3-2-1 version there's nothing that can flash by
// too fast to read. The server still sends one 'countdown' event per second
// (gameplay.py's countdown_to_start) plus a final 'countdown_finished', but
// this only cares about the first and last: show once, hide once. The one
// remaining timing risk — a slow/cold connection buffering the *entire*
// countdown into a single burst, show immediately followed by hide — is
// guarded by a minimum-visible floor so the overlay can't flash for less
// than that even in the worst case.
const countdownShownAt = { game: null, spec: null };
const COUNTDOWN_MIN_VISIBLE_MS = 900;

function showCountdownOverlay(isSpectator) {
  const key = isSpectator ? 'spec' : 'game';
  const overlay = $(key === 'spec' ? 'spec-game-start-overlay' : 'game-start-overlay');
  if (overlay.classList.contains('show')) return;
  countdownShownAt[key] = Date.now();
  overlay.classList.add('show');
}

// The countdown's final tick — clear the overlay so the first real auction
// underneath takes over immediately, no separate "Game Started!" message.
function hideCountdownOverlay(isSpectator) {
  const key = isSpectator ? 'spec' : 'game';
  const overlay = $(key === 'spec' ? 'spec-game-start-overlay' : 'game-start-overlay');
  const shownAt = countdownShownAt[key];
  const elapsed = shownAt ? Date.now() - shownAt : COUNTDOWN_MIN_VISIBLE_MS;
  const wait = Math.max(0, COUNTDOWN_MIN_VISIBLE_MS - elapsed);
  setTimeout(() => {
    overlay.classList.remove('show');
    countdownShownAt[key] = null;
  }, wait);
}

// Points formula mirrors BasePlayer.__calculate_points(): sum of values,
// times the product of multipliers (Passe: -5/×1, Scandale: 0/×0.5,
// Prestige: 0/×2) — see components_module/{disgrace_card,prestige_card}.py.
function computePoints(statusCards) {
  let sum = 0;
  let mult = 1;
  for (const c of statusCards) { sum += c.value; mult *= c.multiplier; }
  return sum * mult;
}

// ---------------------------------------------------------------- state --

let ws = null;
let statusPollTimer = null;
let lastStatus = null;
let pendingJoin = null;
let pendingSpectate = null;
let pendingIdentifyError = null;
let game = null;

// Which room this browser tab is currently looking at — null means "no room
// yet, show the home screen" (public rooms list + host-a-game form). Set
// either from the `?room=` URL param on load (a shared link), by creating a
// game, or by picking/entering a room from the home screen. Kept in the URL
// (via history.replaceState) so a refresh or a shared link lands back in the
// same room instead of the home screen.
let currentRoomCode = null;
let roomsPollTimer = null;

// Reconnection: if a refresh/dropped connection happens mid-game, the
// server keeps your seat (see web_server.py's rejoin-token handling) —
// this is what lets the browser find its way back to it. Stored per-room
// (not just "the" token) so multiple rooms across tabs/history don't clash.
// isReconnecting distinguishes "this IDENTIFY_SUCCESS is a fresh join" vs
// "this is resuming an existing seat" for handlePlayerMessage; reconnectAttempted
// guards against retrying a bad/expired token in a loop.
let isReconnecting = false;
let reconnectAttempted = false;
// Set the moment the user clicks Resign — the client already knows this seat
// is gone for good, so renderForStatus() should never try attemptReconnect()
// for it. Waiting for the server's IDENTIFY_ERROR round-trip instead is
// unreliable: the dev WebSocket server can write trailing bytes after closing
// a just-rejected reconnect socket, which some browsers treat as a framing
// error before the error message ever reaches onmessage.
let hasResigned = false;

// Rematch panel state on the finished screen: null while no vote is
// underway, else {requestedBy, botMix, votes} mirroring web_server.py's
// GameRoom.rematch shape (see REMATCH_UPDATE). rematchBotSeats/
// rematchDefaultBotMix come from the /api/status payload (or a live status
// refresh) each time the finished screen renders — the server, not this
// file, owns how many bot seats are actually available and what they
// default to (see _status_payload's "rematch_bot_seats"/
// "rematch_default_bot_mix").
let currentRematch = null;
let rematchBotSeats = 0;
let rematchDefaultBotMix = [];

function rejoinStorageKey(roomCode) {
  return `hs_rejoin_${roomCode}`;
}
function saveRejoinInfo(roomCode, token, username, name) {
  localStorage.setItem(rejoinStorageKey(roomCode), JSON.stringify({ token, username, name }));
}
function loadRejoinInfo(roomCode) {
  const raw = roomCode && localStorage.getItem(rejoinStorageKey(roomCode));
  if (!raw) return null;
  try {
    const info = JSON.parse(raw);
    return info && info.token && info.username ? info : null;
  } catch (e) {
    return null;
  }
}
function clearRejoinInfo(roomCode) {
  if (roomCode) localStorage.removeItem(rejoinStorageKey(roomCode));
}

// --------------------------------------------------------- saved identity --

// A single persisted {username, name} — the same identity used across every
// room this browser hosts/joins/spectates, so the join/spectate forms below
// don't need to ask again each time (see renderProfileBar/
// applyJoinIdentityDefaults). Deliberately one JSON blob under one key
// rather than separate fields, so it's easy to grow later (e.g. a guestId,
// once there's a real account/guest-login system to attach it to — this is
// meant to become that anonymous identity's storage, not be replaced by it).
// Mirrored into a cookie alongside localStorage: today that's just a second
// place the identity survives (private-browsing tabs, a cleared
// localStorage but not cookies, etc.), but it's also the one persistence
// mechanism a server can read directly without any client JS running,
// which matters the day this becomes a real session cookie.
const PROFILE_STORAGE_KEY = 'hs_profile';

function readCookie(key) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function writeCookie(key, value, days) {
  try {
    const expires = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toUTCString();
    document.cookie = `${key}=${encodeURIComponent(value)}; expires=${expires}; path=/; samesite=lax`;
  } catch (e) {
    // Cookies disabled/blocked — localStorage (the primary copy) still works.
  }
}

function loadProfile() {
  let raw = null;
  try { raw = localStorage.getItem(PROFILE_STORAGE_KEY); } catch (e) { /* private mode, etc. */ }
  if (!raw) raw = readCookie(PROFILE_STORAGE_KEY);
  if (!raw) return null;
  try {
    const profile = JSON.parse(raw);
    return (profile && profile.username && profile.name) ? profile : null;
  } catch (e) {
    return null;
  }
}

function saveProfile(username, name) {
  const value = JSON.stringify({ username, name });
  try { localStorage.setItem(PROFILE_STORAGE_KEY, value); } catch (e) { /* fall through to the cookie */ }
  writeCookie(PROFILE_STORAGE_KEY, value, 365);
}

// Whether the join/spectate screens' "not you?" link has been clicked since
// the last time we landed on a fresh room — guards applyJoinIdentityDefaults
// (called on every lobby status poll, not just once) from stomping over
// someone's in-progress edit to their own name every 1.5s. Reset wherever a
// genuinely new room is entered.
let joinIdentityOverridden = false;

// The top-right chip is the one persistent "profile area" (see
// #profile-chip-wrap in index.html): whenever this browser isn't currently
// locked into a room session, it shows the saved name (or "Guest" — a
// placeholder that itself signals "click me to set this") and opens the
// popover below on click. setBadge() below is what locks it during an
// actual session (connecting/playing/spectating), where the joined
// identity is already fixed and editing the *saved* profile wouldn't
// change anything about the current seat anyway.
function renderProfileChip() {
  const badge = $('connection-badge');
  const profile = loadProfile();
  $('connection-badge-text').textContent = profile ? profile.name : 'Username';
  badge.classList.remove('hidden');
  badge.classList.add('editable');
  // Glows until a real profile is saved (see ensureProfileSet/onSaveProfileClick)
  // -- a passive "this still needs you" cue, gone for good the moment one exists.
  badge.classList.toggle('needs-attention', !profile);
  closeProfilePopover();
  applyJoinIdentityDefaults();
}

function openProfilePopover() {
  const profile = loadProfile();
  $('profile-username').value = profile ? profile.username : '';
  $('profile-display-name').value = profile ? profile.name : '';
  hide($('profile-error'));
  show($('profile-popover'));
}

function closeProfilePopover() {
  hide($('profile-popover'));
  $('profile-username').classList.remove('needs-attention');
}

// Guards the "Host Game" / "Join" actions on the home screen: if this
// browser has never saved a profile, a click would otherwise silently go
// through under the generic "Username" placeholder (see renderProfileChip).
// Opens the popover and glows the username field instead of proceeding, so
// a first-time visitor gets one clear nudge before their first game starts.
// Purely a client-side check against the already-cached profile (see
// loadProfile) -- no network round-trip, so it adds no latency/backend load
// to the host/join request it's guarding. Returns true if the action should
// be aborted (popover opened) so callers can `if (ensureProfileSet(event)) return;`.
// Takes the triggering click event so it can stop it from bubbling up to
// the document-level "click outside the chip closes the popover" listener
// below -- without this, that same click (button, then document, in one
// synchronous dispatch) would close the popover the instant it opens.
function ensureProfileSet(event) {
  if (loadProfile()) return false;
  if (event) event.stopPropagation();
  openProfilePopover();
  const input = $('profile-username');
  input.classList.add('needs-attention');
  input.focus();
  input.addEventListener('input', () => input.classList.remove('needs-attention'), { once: true });
  return true;
}

function onProfileChipClick() {
  if (!$('connection-badge').classList.contains('editable')) return; // locked into a room session
  if ($('profile-popover').classList.contains('hidden')) {
    openProfilePopover();
  } else {
    closeProfilePopover();
  }
}

function onCancelProfileEdit() {
  closeProfilePopover();
}

function onSaveProfileClick() {
  hide($('profile-error'));
  const username = $('profile-username').value.trim();
  const name = $('profile-display-name').value.trim() || username;
  if (!username) { showError($('profile-error'), 'Username is required.'); return; }
  saveProfile(username, name);
  renderProfileChip();
}

// Pre-fills the join screen's username/name fields from the saved profile
// and collapses them behind a "Joining as X — not you?" line, so returning
// to a room (or joining a new one) never re-asks for something already on
// file. Safe to call repeatedly (renderLobby calls this on every status
// poll) — a no-op past the first call unless the profile itself changes,
// and never overwrites an in-progress "not you?" edit (see
// joinIdentityOverridden).
function applyJoinIdentityDefaults() {
  if (joinIdentityOverridden) return;
  const profile = loadProfile();
  if (profile) {
    $('join-username').value = profile.username;
    $('join-name').value = profile.name;
    $('join-as-name').textContent = profile.name;
    show($('join-as-label'));
    hide($('join-identity-fields'));
  } else {
    hide($('join-as-label'));
    show($('join-identity-fields'));
  }
}

function onChangeJoinIdentity() {
  joinIdentityOverridden = true;
  hide($('join-as-label'));
  show($('join-identity-fields'));
}

// Spectating has no recurring-poll render path the way the join screen
// does (screen-spectate-join is only ever shown once per click of "Watch as
// a spectator instead"), so this doesn't need joinIdentityOverridden's
// re-render guard — it just runs once, right when that screen is opened.
function applySpectateIdentityDefaults() {
  const profile = loadProfile();
  if (profile) {
    $('spectate-username').value = profile.username;
    $('spectate-name').value = profile.name;
    $('spectate-as-name').textContent = profile.name;
    show($('spectate-as-label'));
    hide($('spectate-identity-fields'));
  } else {
    hide($('spectate-as-label'));
    show($('spectate-identity-fields'));
  }
}

function onChangeSpectateIdentity() {
  hide($('spectate-as-label'));
  show($('spectate-identity-fields'));
}

// Whether opponents' actual won cards/points are shown, or kept hidden
// behind card-backs, and whether the game-log panel is shown at all — both
// are host-time settings (see host-reveal-cards/host-show-logs in the
// lobby form), fixed for the whole table once the game starts rather than
// a per-player runtime toggle. `status` is whatever /api/status last said
// about this room; defaults to "on" if not known yet.
function resetGameState(myUsername, status) {
  game = {
    round: 0,
    card: null,
    maxBid: 0,
    myAuctionBid: 0, // my own cumulative committed bid for the *current* auction only
    turnPlayer: null,
    myUsername,
    myPoints: 0,
    myStatusCards: [],
    selectedBid: new Set(),
    opponents: {}, // username -> {name, statusCards: [], active: true, outOfAuction: false}
    // The real post-shuffle seat/turn order (see gameplay.py's player_order
    // broadcast) -- empty until that arrives, which renderOpponents falls
    // back to plain insertion order for.
    playerOrder: [],
    revealCards: status ? status.reveal_cards !== false : true,
    showLogs: status ? status.show_logs !== false : true,
    // The room's fixed per-move timer (seconds, or null/undefined for no
    // limit) — used only to scale the move-timer's "urgent" warning window
    // (see urgentWindowSeconds), not re-sent per move.
    turnTimeLimit: status ? status.turn_time_limit : null,
  };
  applyRoomDisplaySettings();
}

// Reflects the room's fixed reveal-cards/show-logs settings in the UI: a
// read-only status label (there's no toggle anymore — see resetGameState)
// and hiding the *player* log panel when the host turned it off. Spectators
// always get the log regardless of that setting — they have no toasts/
// opponent-panel context of their own to fall back on, so it's their main
// way to follow what's happening, not an optional extra like it is for
// players.
function applyRoomDisplaySettings() {
  const label = game.revealCards ? 'Cards revealed' : 'Cards hidden';
  $('reveal-cards-status').textContent = label;
  $('spec-reveal-cards-status').textContent = label;
  $('game-log').closest('details').classList.toggle('hidden', !game.showLogs);
}

function seedOpponents(status, myUsername) {
  (status.joined || []).forEach((p) => {
    if (p.username === myUsername) return;
    game.opponents[p.username] = { name: p.name, statusCards: [], active: true, outOfAuction: false };
  });
}

function ensureOpponent(username) {
  if (!game.opponents[username]) {
    game.opponents[username] = { name: username, statusCards: [], active: true, outOfAuction: false };
  }
  return game.opponents[username];
}

// ------------------------------------------------------------------ boot --

document.addEventListener('DOMContentLoaded', () => {
  wireStaticHandlers();
  renderProfileChip();
  currentRoomCode = new URLSearchParams(location.search).get('room');
  if (currentRoomCode) {
    refreshStatus();
    startPolling();
  } else {
    showScreen('screen-host-setup');
    showHomeTiles();
    startRoomsPolling();
  }
});

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

// Puts `room` in both the URL (so a refresh/shared link returns to the same
// room) and the module-level currentRoomCode every subsequent API/WS call
// reads from. Validates the code first (rather than just switching screens
// and letting the generic !status.exists path bounce back) so a mistyped
// room code gets a clear error right on the home screen instead of silently
// doing nothing.
async function enterRoom(roomCode, event) {
  if (ensureProfileSet(event)) return;
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
  currentRoomCode = roomCode;
  history.replaceState(null, '', `?room=${encodeURIComponent(roomCode)}`);
  stopRoomsPolling();
  lastStatus = status;
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
function showHomeTile(target) {
  hide($('home-tiles'));
  HOME_TILE_TARGETS.forEach((t) => $(`home-panel-${t}`).classList.toggle('hidden', t !== target));
}
function showHomeTiles() {
  show($('home-tiles'));
  HOME_TILE_TARGETS.forEach((t) => hide($(`home-panel-${t}`)));
}

// clearRejoin is false only for the "clicked the High Society title mid-game"
// path (see onHomeLinkClick) — that's meant to behave like closing the tab,
// which stays reconnectable, not like clicking "Return to Home" after the
// game's already over, which has nothing left to reconnect to.
function leaveToHome(clearRejoin = true) {
  if (clearRejoin) clearRejoinInfo(currentRoomCode);
  currentRoomCode = null;
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
// leaving should warn first, shared by the tab-close warning below and the
// "High Society" home-link click.
function isActivelyPlayingLiveGame() {
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
async function onHomeLinkClick() {
  const midGame = isActivelyPlayingLiveGame();
  if (midGame) {
    const ok = await confirmDialog(
      'Leave this game and go back to the home screen? Your seat stays open to rejoin from this device.',
      'Leave',
    );
    if (!ok) return;
  }
  if (ws) { ws.close(); ws = null; }
  leaveToHome(!midGame);
}

function startRoomsPolling() {
  if (roomsPollTimer) return;
  refreshRoomsList();
  roomsPollTimer = setInterval(refreshRoomsList, 2000);
}
function stopRoomsPolling() {
  if (roomsPollTimer) { clearInterval(roomsPollTimer); roomsPollTimer = null; }
}

async function refreshRoomsList() {
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
    container.innerHTML = '<p class="muted">No public games open right now — host one below!</p>';
    return;
  }
  container.innerHTML = '';
  rooms.forEach((r) => {
    const row = document.createElement('div');
    row.className = 'room-row';
    const label = document.createElement('span');
    label.textContent = `Room ${r.room_code} — ${r.joined}/${r.seats} seats filled`;
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

async function refreshStatus() {
  if (!currentRoomCode) return;
  let status;
  try {
    status = await fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode)}`);
  } catch (e) {
    return; // transient network hiccup — next poll (or manual reload) retries
  }
  lastStatus = status;
  renderForStatus(status);
}

function renderForStatus(status) {
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
      ? "You resigned from this game — you can watch as a spectator."
      : 'A game is already in progress — you can watch as a spectator.';
  }
}

function startPolling() {
  if (statusPollTimer) return;
  statusPollTimer = setInterval(refreshStatus, 1500);
}
function stopPolling() {
  if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer = null; }
}

// Separate from the main status poll (which onJoin() stops the moment you
// connect — see connectPlayerSocket) because "waiting in the lobby after
// joining" is a distinct phase: nothing about the shared game/opponents
// state has started yet, but you still want to see the seat count update
// live as other people join, and to know when there's still an empty seat
// worth filling with a bot (see onAddBot). Self-cancels once the room
// leaves "lobby" (game started), so it never runs for the rest of the game.
let waitingRoomPollTimer = null;

async function _tickWaitingRoomStatus() {
  let status;
  try {
    status = await fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode)}`);
  } catch (e) {
    return;
  }
  if (!status.exists || status.state !== 'lobby') {
    stopWaitingRoomPolling();
    return;
  }
  const names = status.joined.map((p) => `${p.name}${p.is_bot ? ' 🤖' : ''}`).join(', ') || 'nobody yet';
  $('lobby-status').textContent = `Seats filled: ${status.joined.length}/${status.seats} — ${names}`;
}

function startWaitingRoomPolling() {
  if (waitingRoomPollTimer) return;
  // Without this immediate call, setInterval's first tick is 1.5s out --
  // "Seats filled: 0/3 — nobody yet" (whatever lobby-status showed before
  // you joined) sits directly next to "You're in!" for that whole window,
  // visibly contradicting it. Matches startRoomsPolling()'s same fix below.
  _tickWaitingRoomStatus();
  waitingRoomPollTimer = setInterval(_tickWaitingRoomStatus, 1500);
}
function stopWaitingRoomPolling() {
  if (waitingRoomPollTimer) { clearInterval(waitingRoomPollTimer); waitingRoomPollTimer = null; }
}

async function onAddBot() {
  hide($('add-bot-error'));
  const botType = $('waiting-bot-type').value;
  try {
    await fetchJSON('/api/add_bot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room: currentRoomCode, bot_type: botType }),
    });
    // The waiting-room poll (already running — see startWaitingRoomPolling)
    // picks up the new seat count on its own next tick; if this bot filled
    // the last seat, the game itself starts server-side and this player's
    // already-open WebSocket starts receiving real game messages naturally.
  } catch (e) {
    showError($('add-bot-error'), e.message);
  }
}

function renderLobby(status) {
  showScreen('screen-join');
  $('join-form').classList.remove('hidden');
  $('join-waiting').classList.add('hidden');
  applyJoinIdentityDefaults();
  const visibilityNote = status.visibility === 'private' ? ' (private — share this code with friends)' : ' (public)';
  $('room-code-text').textContent = `Room code: ${status.room_code}${visibilityNote}`;
  show($('room-code-display'));
  const names = status.joined.map((p) => `${p.name}${p.is_bot ? ' 🤖' : ''}`).join(', ') || 'nobody yet';
  $('lobby-status').textContent = `Seats filled: ${status.joined.length}/${status.seats} — ${names}`;
  if (pendingIdentifyError) {
    showError($('join-error'), pendingIdentifyError);
    pendingIdentifyError = null;
  }
}

function renderFinished(status) {
  clearRejoinInfo(currentRoomCode); // game's over — nothing left to reconnect to
  showScreen('screen-finished');
  // The money-eliminated player (see gameplay.py's determine_winner —
  // distinct from simply "didn't have the highest score") was never in
  // contention at all, so they're sorted to the very bottom regardless of
  // points; everyone else sorts by points as before.
  const standings = (status.final_standings || []).slice().sort((a, b) => {
    if (!!a.eliminated !== !!b.eliminated) return a.eliminated ? 1 : -1;
    return b.points - a.points;
  });
  const winners = new Set(status.winners || []);

  const trophy = $('finished-trophy');
  if (winners.size === 1) {
    trophy.textContent = '🏆';
    $('finished-headline').textContent = `${[...winners][0]} wins!`;
  } else if (winners.size > 1) {
    trophy.textContent = '🤝';
    $('finished-headline').textContent = `Tie: ${[...winners].join(', ')}`;
  } else {
    trophy.textContent = '🎲';
    $('finished-headline').textContent = 'Game over';
  }
  // Restart the entrance animation even if this exact status was already
  // rendered once (e.g. a stray poll) — removing and re-adding the class
  // forces the browser to replay the @keyframes rather than no-op.
  trophy.classList.remove('enter');
  void trophy.offsetWidth; // eslint-disable-line no-unused-expressions -- force reflow so the class removal actually takes effect first
  trophy.classList.add('enter');

  const rows = standings.map((s, i) => {
    const isWinner = winners.has(s.username);
    const state = isWinner ? 'winner' : s.eliminated ? 'eliminated' : (s.active === false ? 'inactive' : 'lost');
    // Full reasoning lives in the "How is the winner decided?" details right
    // below the table — this tag just flags *which* row it applies to, kept
    // short, with a hover title for anyone who wants the one-line version
    // without leaving the row.
    const tag = isWinner ? ' 🏆'
      : s.eliminated ? ' <span class="standing-tag" title="Eliminated from winning for having the least money left">(least money)</span>'
      : (s.active === false ? ' (left the game)' : '');
    return `
    <div class="standing-row ${state}" style="animation-delay: ${i * 90}ms">
      <span class="name">${escapeHtml(s.username)}${tag}</span>
      <span>Points: ${s.points}</span>
      <span>Money left: ${s.money_left}</span>
    </div>`;
  }).join('');
  $('standings-table').innerHTML = rows || '<p class="muted">No standings available.</p>';

  currentRematch = status.rematch || null;
  rematchBotSeats = status.rematch_bot_seats || 0;
  rematchDefaultBotMix = status.rematch_default_bot_mix || [];
  renderRematchPanel();
}

// ----------------------------------------------------------- rematch flow --

// Only a still-connected player (as opposed to a spectator, or a player
// viewing the results via a status poll with no live socket — see
// connectSpectatorSocket's onclose, which nulls `ws` before this ever runs)
// has a channel to actually request/vote on a rematch over, so this hides
// the whole panel for anyone else rather than showing controls that
// couldn't do anything.
function renderRematchPanel() {
  const panel = $('rematch-panel');
  if (!ws || ws.readyState !== WebSocket.OPEN || !game.myUsername) {
    hide(panel);
    return;
  }
  show(panel);
  hide($('rematch-error'));

  const requestBtn = $('btn-request-rematch');
  const form = $('rematch-bot-form');
  const statusBox = $('rematch-status');
  const voteActions = $('rematch-vote-actions');

  if (!currentRematch) {
    show(requestBtn);
    hide(form);
    hide(statusBox);
    return;
  }

  hide(requestBtn);
  hide(form);
  show(statusBox);

  const { requestedBy, votes } = currentRematch;
  const iHaveAccepted = votes[game.myUsername] === true;
  const waitingOn = Object.keys(votes).filter((n) => votes[n] !== true);

  if (iHaveAccepted) {
    hide(voteActions);
    $('rematch-status-text').textContent = waitingOn.length
      ? `Waiting on ${waitingOn.join(', ')} to accept the rematch…`
      : 'Everyone accepted — starting the rematch…';
  } else {
    show(voteActions);
    $('rematch-status-text').textContent = `${requestedBy} wants a rematch. Accept?`;
  }
}

function fillRematchBotForm(mix) {
  const counts = { easy: 0, medium: 0, hard: 0 };
  mix.forEach((b) => { if (counts[b] !== undefined) counts[b] += 1; });
  $('rematch-bot-easy').value = counts.easy;
  $('rematch-bot-medium').value = counts.medium;
  $('rematch-bot-hard').value = counts.hard;
  $('rematch-bot-hint').textContent = rematchBotSeats
    ? `${rematchBotSeats} bot seat${rematchBotSeats === 1 ? '' : 's'} to fill — same as last time by default, but changeable.`
    : 'No bot seats this time — every seat is a returning player.';
}

function onRequestRematchClick() {
  if (rematchBotSeats > 0) {
    fillRematchBotForm(rematchDefaultBotMix);
    hide($('btn-request-rematch'));
    show($('rematch-bot-form'));
  } else {
    sendRematchRequest([]);
  }
}

function onCancelRematchForm() {
  hide($('rematch-bot-form'));
  show($('btn-request-rematch'));
}

function onSendRematchRequest() {
  const counts = {
    easy: parseInt($('rematch-bot-easy').value || '0', 10),
    medium: parseInt($('rematch-bot-medium').value || '0', 10),
    hard: parseInt($('rematch-bot-hard').value || '0', 10),
  };
  const botMix = [];
  for (const [type, n] of Object.entries(counts)) for (let i = 0; i < n; i += 1) botMix.push(type);
  if (botMix.length !== rematchBotSeats) {
    showError($('rematch-error'), `Choose exactly ${rematchBotSeats} bot${rematchBotSeats === 1 ? '' : 's'} total.`);
    return;
  }
  sendRematchRequest(botMix);
}

function sendRematchRequest(botMix) {
  hide($('rematch-bot-form'));
  ws.send(JSON.stringify({ message_type: 'REMATCH_REQUEST', data: { bot_mix: botMix } }));
}

function onAcceptRematch() {
  ws.send(JSON.stringify({ message_type: 'REMATCH_VOTE', data: { accept: true } }));
}

function onDeclineRematch() {
  ws.send(JSON.stringify({ message_type: 'REMATCH_VOTE', data: { accept: false } }));
}

function showRematchDeclinedNotice(declinedBy) {
  show($('rematch-status'));
  hide($('rematch-vote-actions'));
  $('rematch-status-text').textContent = `${declinedBy} declined the rematch.`;
  // The next real update (a fresh request, or none at all) re-renders this
  // properly; this is just a few seconds of "here's what just happened"
  // before falling back to the normal "Request Rematch" state.
  setTimeout(() => { if (!currentRematch) renderRematchPanel(); }, 4000);
}

// ------------------------------------------------------------- host flow --

function wireStaticHandlers() {
  $('confirm-modal-cancel').addEventListener('click', () => _resolveConfirmDialog(false));
  $('confirm-modal-confirm').addEventListener('click', () => _resolveConfirmDialog(true));
  document.querySelectorAll('.home-tile').forEach((btn) => {
    btn.addEventListener('click', () => showHomeTile(btn.dataset.homeTarget));
  });
  document.querySelectorAll('.home-back').forEach((btn) => {
    btn.addEventListener('click', showHomeTiles);
  });
  $('btn-create-game').addEventListener('click', onCreateGame);
  $('btn-join-by-code').addEventListener('click', onJoinByCode);
  $('btn-copy-room-link').addEventListener('click', onCopyRoomLink);
  $('btn-add-bot').addEventListener('click', onAddBot);
  $('btn-join').addEventListener('click', onJoin);
  $('btn-spectate-link').addEventListener('click', () => {
    applySpectateIdentityDefaults();
    showScreen('screen-spectate-join');
  });
  $('btn-back-to-join').addEventListener('click', () => { showScreen('screen-join'); refreshStatus(); });
  $('btn-spectate-join').addEventListener('click', onSpectateJoin);
  $('btn-new-game').addEventListener('click', () => leaveToHome());
  $('connection-badge').addEventListener('click', onProfileChipClick);
  $('btn-cancel-profile-edit').addEventListener('click', onCancelProfileEdit);
  $('btn-save-profile').addEventListener('click', onSaveProfileClick);
  // Standard popover UX: a click anywhere outside the chip/popover itself
  // closes it, same as a browser's own menus.
  document.addEventListener('click', (e) => {
    if (!$('profile-chip-wrap').contains(e.target)) closeProfilePopover();
  });
  $('btn-change-join-identity').addEventListener('click', onChangeJoinIdentity);
  $('btn-change-spectate-identity').addEventListener('click', onChangeSpectateIdentity);
  $('home-link').addEventListener('click', onHomeLinkClick);
  $('btn-stop-watching').addEventListener('click', onHomeLinkClick);
  $('home-link').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onHomeLinkClick(); }
  });
  $('btn-place-bid').addEventListener('click', onPlaceBid);
  $('btn-pass').addEventListener('click', onPass);
  $('btn-resign').addEventListener('click', onResign);
  $('btn-discard-painting').addEventListener('click', onDiscardPainting);
  $('btn-spec-chat-send').addEventListener('click', onSpecChatSend);
  $('spec-chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') onSpecChatSend(); });
  $('spec-chat-target-toggle').addEventListener('change', (e) => {
    $('spec-chat-input').placeholder = e.target.checked ? 'Message spectators only…' : 'Message everyone…';
  });
  $('btn-player-chat-send').addEventListener('click', onPlayerChatSend);
  $('player-chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') onPlayerChatSend(); });
  $('btn-request-rematch').addEventListener('click', onRequestRematchClick);
  $('btn-cancel-rematch-form').addEventListener('click', onCancelRematchForm);
  $('btn-send-rematch-request').addEventListener('click', onSendRematchRequest);
  $('btn-accept-rematch').addEventListener('click', onAcceptRematch);
  $('btn-decline-rematch').addEventListener('click', onDeclineRematch);

  window.addEventListener('beforeunload', (e) => {
    if (isActivelyPlayingLiveGame()) {
      e.preventDefault();
      e.returnValue = 'Leaving now drops you from the game — there is no reconnect.';
    }
  });
}

async function onCreateGame(event) {
  if (ensureProfileSet(event)) return;
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
  const body = {
    seats,
    bot_mix: botMix,
    // No seed field in the UI on purpose — a reproducible game is a
    // developer/testing concern (real training/testing can go through the
    // backend directly), not something a hosting player needs to see.
    bot_think_time: parseFloat($('host-think-time').value || '1.5'),
    visibility: $('host-visibility-private').checked ? 'private' : 'public',
    turn_time_limit: turnTimeRaw ? parseFloat(turnTimeRaw) : null,
    reveal_cards: $('host-reveal-cards').checked,
    show_logs: $('host-show-logs').checked,
  };
  try {
    const status = await fetchJSON('/api/create_game', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    stopRoomsPolling();
    joinIdentityOverridden = false; // a genuinely new room — start from the saved profile again
    currentRoomCode = status.room_code;
    history.replaceState(null, '', `?room=${encodeURIComponent(status.room_code)}`);
    lastStatus = status;
    renderLobby(status);
    startPolling();
  } catch (e) {
    showError($('host-error'), e.message);
  }
}

function onJoinByCode(event) {
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
async function onCopyRoomLink() {
  if (!currentRoomCode) return;
  const url = `${location.origin}${location.pathname}?room=${encodeURIComponent(currentRoomCode)}`;
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

// ------------------------------------------------------------- join flow --

function respondIdentify(socket, pending, msg) {
  const wantsUsername = /username/i.test(msg.prompt);
  const answer = wantsUsername ? pending.username : pending.name;
  socket.send(JSON.stringify({ message_type: 'IDENTIFY_ACK', prompt: answer }));
}

function onJoin() {
  hide($('join-error'));
  const username = $('join-username').value.trim();
  const name = $('join-name').value.trim() || username;
  if (!username) { showError($('join-error'), 'Username is required.'); return; }
  pendingJoin = { username, name };
  saveProfile(username, name); // this device's identity going forward — see loadProfile
  stopPolling();
  resetGameState(username, lastStatus);
  if (lastStatus) seedOpponents(lastStatus, username);
  connectPlayerSocket();
}

function connectPlayerSocket() {
  ws = new WebSocket(wsUrl(`/ws?room=${encodeURIComponent(currentRoomCode)}`));
  ws.onmessage = (evt) => handlePlayerMessage(JSON.parse(evt.data));
  ws.onclose = () => { ws = null; refreshStatus(); };
  setBadge('connecting…');
}

// Called when the room turns out to already be starting/in_progress and we
// have no open socket (fresh page load after a refresh, or the tab was just
// sitting on some other screen when the game started) — see
// renderForStatus. Returns true if a stored rejoin token existed and a
// reconnect attempt was actually started (caller should stop polling and
// wait for the result), false if there was nothing to try (falls through
// to the normal "watch as a spectator" message).
function attemptReconnect() {
  const info = loadRejoinInfo(currentRoomCode);
  if (!info) return false;

  isReconnecting = true;
  resetGameState(info.username, lastStatus);
  if (lastStatus) seedOpponents(lastStatus, info.username);
  ws = new WebSocket(wsUrl(
    `/ws?room=${encodeURIComponent(currentRoomCode)}&rejoin_token=${encodeURIComponent(info.token)}`,
  ));
  ws.onmessage = (evt) => handlePlayerMessage(JSON.parse(evt.data));
  ws.onclose = () => { ws = null; refreshStatus(); };
  setBadge('reconnecting…');
  return true;
}

function handlePlayerMessage(msg) {
  switch (msg.message_type) {
    case 'IDENTIFY':
      respondIdentify(ws, pendingJoin, msg);
      break;
    case 'IDENTIFY_ERROR':
      if (isReconnecting) {
        // Token was invalid/expired (e.g. resigned, the game finished in
        // the meantime, or someone else already reconnected with it) — drop
        // it and fall back to the normal "already in progress" message
        // rather than retrying it forever. Show the server's actual reason
        // when it gave one (e.g. "You resigned...") instead of always the
        // generic fallback text.
        isReconnecting = false;
        clearRejoinInfo(currentRoomCode);
        ws.close();
        showScreen('screen-join');
        $('join-form').classList.add('hidden');
        $('join-waiting').classList.add('hidden');
        $('lobby-status').textContent = msg.prompt || 'A game is already in progress — you can watch as a spectator.';
      } else {
        pendingIdentifyError = msg.prompt;
        ws.close();
      }
      break;
    case 'IDENTIFY_SUCCESS':
      if (isReconnecting) {
        isReconnecting = false;
        setBadge(`Playing as ${game.myUsername}`);
        ensureGameScreenVisible(false);
      } else {
        $('join-form').classList.add('hidden');
        $('join-waiting').classList.remove('hidden');
        setBadge(`Playing as ${game.myUsername}`);
        startWaitingRoomPolling();
        if (msg.data && msg.data.rejoin_token) {
          saveRejoinInfo(currentRoomCode, msg.data.rejoin_token, pendingJoin.username, pendingJoin.name);
        }
      }
      break;
    // These three arrive only on the finished screen (see
    // web_server.py's _broadcast_rematch_update/_maybe_start_rematch) —
    // handled here rather than in applyGameMessage() so they skip its
    // unconditional ensureGameScreenVisible() call, which would otherwise
    // yank the screen back to the live game panel while a rematch is still
    // just being voted on.
    case 'REMATCH_UPDATE':
      currentRematch = { requestedBy: msg.data.requested_by, botMix: msg.data.bot_mix, votes: msg.data.votes };
      renderRematchPanel();
      break;
    case 'REMATCH_DECLINED':
      currentRematch = null;
      showRematchDeclinedNotice(msg.data.declined_by);
      break;
    case 'REMATCH_STARTING': {
      currentRematch = null;
      const myUsername = game.myUsername;
      resetGameState(myUsername, lastStatus);
      // resetGameState only resets the in-memory model — the DOM still shows
      // whatever the *previous* game last rendered (final points, opponents'
      // won cards, auction count) until the new game's first live event
      // overwrites it, which can be a couple of seconds away (see
      // countdown_to_start). Force it to reflect the fresh, empty state
      // immediately instead of flashing the old game's numbers first.
      hide($('move-panel'));
      $('move-panel').classList.remove('pending');
      renderAuctionPanel(false);
      renderMyPanel();
      renderMoneyChips([]);
      // Resign works anytime once in a game (see onResign) -- re-enable it
      // for the fresh game immediately rather than waiting for its first
      // PLAYER_STATE/PLAYER_MOVE.
      $('btn-resign').disabled = false;
      ensureGameScreenVisible(false);
      fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode)}`).then((status) => {
        lastStatus = status;
        seedOpponents(status, myUsername);
        renderOpponents(false);
      }).catch(() => {});
      break;
    }
    default:
      applyGameMessage(msg, false);
  }
}

// --------------------------------------------------------- spectate flow --

function onSpectateJoin() {
  hide($('spectate-error'));
  const name = $('spectate-name').value.trim();
  const username = $('spectate-username').value.trim();
  if (!name || !username) { showError($('spectate-error'), 'Both fields are required.'); return; }
  pendingSpectate = { username, name };
  saveProfile(username, name); // this device's identity going forward — see loadProfile
  stopPolling();
  resetGameState(null, lastStatus);
  fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode)}`)
    .then((status) => {
      seedOpponents(status, null);
      game.revealCards = status.reveal_cards !== false;
      game.showLogs = status.show_logs !== false;
      applyRoomDisplaySettings();
    }).catch(() => {});
  connectSpectatorSocket();
}

function connectSpectatorSocket() {
  ws = new WebSocket(wsUrl(`/ws_spectate?room=${encodeURIComponent(currentRoomCode)}`));
  ws.onmessage = (evt) => handleSpectatorMessage(JSON.parse(evt.data));
  // The server closes every spectator's connection right after the game
  // ends (see GameRoom.run_game in web_server.py) — same signal the player
  // side already uses (onJoin's connectPlayerSocket) to notice the game
  // finished and switch to the results screen. This was previously a no-op
  // here, so a spectator's browser just sat on the live table forever after
  // the game actually ended, never showing results at all.
  ws.onclose = () => { ws = null; refreshStatus(); };
  showScreen('screen-spectate');
  setBadge('spectating');
}

function handleSpectatorMessage(msg) {
  switch (msg.message_type) {
    case 'IDENTIFY':
      respondIdentify(ws, pendingSpectate, msg);
      break;
    case 'IDENTIFY_ERROR':
      showError($('spectate-error'), msg.prompt);
      ws.close();
      showScreen('screen-spectate-join');
      break;
    case 'IDENTIFY_SUCCESS':
      break;
    default:
      applyGameMessage(msg, true);
  }
}

// A CHAT message is never echoed back to its own sender over the wire (see
// PLAYING.md) — that's a relay-layer rule to avoid double-delivery, not a
// reason to leave the sender's own chat log blank. Appending it locally,
// formatted the same way an incoming one would be, keeps "did that actually
// send?" from ever being a question.
function appendChatLine(elId, text) {
  const el = $(elId);
  const p = document.createElement('div');
  p.textContent = text;
  el.appendChild(p);
  el.scrollTop = el.scrollHeight;
}

function onSpecChatSend() {
  const input = $('spec-chat-input');
  const text = input.value.trim();
  if (!text || !ws) return;
  // See web_server.py's _spectator_chat_listener: "spectators" reaches only
  // other spectators, anything else (including omitting the field) reaches
  // everyone — players included.
  const target = $('spec-chat-target-toggle').checked ? 'spectators' : 'all';
  ws.send(JSON.stringify({ message_type: 'CHAT', prompt: text, target }));
  appendChatLine('spec-chat-log', `💬 You${target === 'spectators' ? ' (spectators only)' : ''}: ${text}`);
  input.value = '';
}

function onPlayerChatSend() {
  const input = $('player-chat-input');
  const text = input.value.trim();
  if (!text || !ws) return;
  // Reaches every other player + all spectators (see web_server.py's
  // _relay_player_chat) — no "target" selector for players, unlike
  // spectators, since chatting to a subset of the table doesn't make sense
  // from a player's own seat.
  ws.send(JSON.stringify({ message_type: 'CHAT', prompt: text }));
  appendChatLine('player-chat-log', `💬 You: ${text}`);
  input.value = '';
}

// --------------------------------------------------------- game reducer --

function ensureGameScreenVisible(isSpectator) {
  const id = isSpectator ? 'screen-spectate' : 'screen-game';
  if ($(id).classList.contains('hidden')) showScreen(id);
}

function logLine(text, isSpectator) {
  if (!text) return;
  const el = $(isSpectator ? 'spec-game-log' : 'game-log');
  const p = document.createElement('div');
  p.textContent = text;
  el.appendChild(p);
  el.scrollTop = el.scrollHeight;
}

// gameplay.py broadcasts a plain-text GLOBAL_EVENT narration line right next
// to most of the structured events this UI already renders from
// AUCTION_UPDATE (turn/bid/pass/fold/quit/auction_start) and AUCTION_RESULT
// (the win announcement) — see gameplay.py's _broadcast_auction_update. Skip
// re-logging those specific plain-text lines so the log doesn't show every
// event twice; every other GLOBAL_EVENT (countdown, green card, faux pas,
// final standings/winner) has no structured counterpart and is still logged.
// Coupled to the exact wording/emoji gameplay.py uses today — if that prose
// changes, update this alongside it.
const DUPLICATE_NARRATION_PATTERNS = [
  /^Auctioning:/,
  /^💀 Disgrace Auction started for:/,
  /^💰 /,
  /^⚪ /,
  /^❌ /,
  /^💢 /,
  /wins the auction for/,
];
function isDuplicateOfStructuredEvent(text) {
  return DUPLICATE_NARRATION_PATTERNS.some((re) => re.test(text.trim()));
}

function applyGameMessage(msg, isSpectator) {
  ensureGameScreenVisible(isSpectator);
  switch (msg.message_type) {
    case 'GLOBAL_EVENT': {
      const d = msg.data;
      if (d && d.event === 'faux_pas_discard') {
        const isOpponent = d.player !== game.myUsername;
        if (isOpponent) {
          const o = ensureOpponent(d.player);
          o.statusCards = o.statusCards.filter((c) => c.value !== d.discarded_value);
          renderOpponents(isSpectator);
          enqueueEvent(isSpectator, game.revealCards
            ? `${d.player} discarded ${d.discarded_value}`
            : `${d.player} discarded a painting`, 'disgrace');
        }
      } else if (d && d.event === 'green_card_revealed') {
        // REVEAL_GREEN — the limit-th (final) green card ends the game
        // immediately, so it gets a distinct, unmissable overlay instead of
        // just another toast in the queue (see showFinalGreenOverlay).
        if (d.is_final) {
          showFinalGreenOverlay(isSpectator, d.count);
        } else {
          enqueueEvent(isSpectator, `🟢 Green card revealed (${d.count})`, 'green');
        }
      } else if (d && d.event === 'opponent_state_sync') {
        // Reconnect catch-up only (see web_server.py's
        // _send_reconnect_catchup): restores what this specific opponent's
        // status-card panel should already show (built up over the whole
        // game via individual AUCTION_RESULT broadcasts we missed while
        // disconnected). Silent — no toast, no log line.
        if (d.username !== game.myUsername) {
          const o = ensureOpponent(d.username);
          o.name = d.name;
          o.active = d.active;
          o.statusCards = d.status_cards;
          renderOpponents(isSpectator);
        }
      } else if (d && d.event === 'player_resigned') {
        // An out-of-turn resign (see web_server.py's on_resign) -- unlike
        // the in-turn quit path (an AUCTION_UPDATE "kind":"quit", handled in
        // applyAuctionUpdate), this can happen at any moment, so it's its
        // own plain event rather than tied to a specific card's auction.
        if (d.player !== game.myUsername) {
          const o = ensureOpponent(d.player);
          o.active = false;
          renderOpponents(isSpectator);
          enqueueEvent(isSpectator, `${d.player} resigned`, 'quit');
        }
      } else if (d && d.event === 'player_reconnected') {
        // Counterpart to player_resigned above -- a dropped connection
        // greys an opponent's tile out via the exact same o.active flag (see
        // the AUCTION_UPDATE "kind":"quit" path, since a dead transport
        // during their own turn looks identical to a quit from the engine's
        // side), but nothing previously told other browsers when that
        // player actually came back, so the tile just stayed stuck "(out)"
        // forever even once the seat was live again.
        if (d.player !== game.myUsername) {
          const o = ensureOpponent(d.player);
          o.active = true;
          renderOpponents(isSpectator);
        }
      } else if (d && d.event === 'player_order') {
        game.playerOrder = d.usernames;
        renderOpponents(isSpectator);
      } else if (d && d.event === 'spectator_count') {
        // Player-only -- spectators already see the full player roster, so
        // a count of their own kind adds nothing for them (see
        // UX_AUDIT.md #2 for why players needed this and had nothing).
        if (!isSpectator) {
          const el = $('spectator-count-status');
          if (d.count > 0) {
            el.textContent = `${d.count} watching`;
            show(el);
          } else {
            hide(el);
          }
        }
      } else if (d && d.event === 'countdown') {
        showCountdownOverlay(isSpectator);
      } else if (d && d.event === 'countdown_finished') {
        hideCountdownOverlay(isSpectator);
      } else if (d && d.event === 'game_over') {
        // The connection is deliberately kept open past game-end now (see
        // web_server.py's GameRoom.run_game — a rematch reuses it), so
        // nothing closes this socket to trigger the old ws.onclose ->
        // refreshStatus() transition to the results screen. This is that
        // signal instead.
        //
        // If the game just ended via the 4th-green-card overlay, give it
        // the same ~3.5s on screen it always intended to have (see
        // showFinalGreenOverlay) before switching to results — otherwise
        // this fires right on its heels and the overlay flashes past
        // unread. A normal deck-exhaustion ending never showed that
        // overlay, so this is a no-op wait there.
        const elapsedSinceGreen = finalGreenOverlayShownAt ? Date.now() - finalGreenOverlayShownAt : Infinity;
        const wait = Math.max(0, 3500 - elapsedSinceGreen);
        setTimeout(() => {
          finalGreenOverlayShownAt = null;
          refreshStatus();
        }, wait);
      }
      if (msg.prompt && !isDuplicateOfStructuredEvent(msg.prompt)) logLine(msg.prompt.trim(), isSpectator);
      break;
    }
    case 'AUCTION_UPDATE':
      applyAuctionUpdate(msg, isSpectator);
      break;
    case 'AUCTION_RESULT':
      applyAuctionResult(msg, isSpectator);
      break;
    case 'PLAYER_STATE':
      if (!isSpectator) applyPlayerState(msg);
      break;
    case 'PLAYER_MOVE':
      if (!isSpectator) applyPlayerMove(msg);
      break;
    case 'PLAYER_MOVE_TIMER':
      // Sent once per move (including once per retry after an invalid
      // input), carrying how many seconds are left at that instant — the
      // server doesn't tick this down itself, so we run a local countdown
      // from this starting point (see startMoveTimer). Only ever sent to
      // the specific player whose turn it is (see NetworkPlayer.get_bid),
      // never broadcast, so spectators never receive this message type.
      if (!isSpectator && msg.data && typeof msg.data.seconds_remaining === 'number') {
        startMoveTimer(msg.data.seconds_remaining);
      }
      break;
    case 'INPUT_ERROR':
      if (!isSpectator) {
        showError($('move-error'), msg.prompt);
        // onPlaceBid() optimistically pends the panel the instant a bid is
        // sent, before the server has actually validated it (e.g. an
        // insufficient raise) — undo that here so a rejected bid looks
        // exactly like the "select at least one money card" case, which
        // never pends in the first place: an error message, panel still
        // fully interactive, not the disabled "waiting for the table" look.
        $('move-panel').classList.remove('pending');
      }
      break;
    case 'CHAT':
      appendChatLine(isSpectator ? 'spec-chat-log' : 'player-chat-log', msg.prompt);
      break;
    default:
      break; // GLOBAL_MOVE_INFO, PLAYER_INFO: superseded by the structured messages above
  }
}

function applyAuctionUpdate(msg, isSpectator) {
  const d = msg.data;
  // Persistent state updates immediately and unconditionally — it must
  // never lag behind or wait on the transient toast queue below, since it's
  // the actual shared game state, not decoration.
  game.round = d.round_number;
  game.card = d.card;
  if (typeof d.max_bid === 'number') game.maxBid = d.max_bid;

  // A player who joined the room *after* this browser's own seedOpponents()
  // snapshot was taken (see onJoin — it seeds once, from whatever /api/status
  // said right before connecting) would otherwise stay invisible in the
  // Opponents panel until they happened to win a card or take a Faux Pas —
  // the only two spots that used to call ensureOpponent(). Every other event
  // below now does the same create-if-missing, so a real opponent shows up
  // the moment they take their very first action (usually within round 1),
  // not whenever the first auction happens to resolve.
  // game.myUsername is null for spectators, so "!== game.myUsername" is
  // always true for them — every player they see is tracked as one of
  // game.opponents, which is exactly right since a spectator has no "my side".
  if (d.kind === 'auction_start') {
    game.maxBid = 0;
    game.myAuctionBid = 0;
    game.turnPlayer = d.starting_player;
    if (d.starting_player !== game.myUsername) ensureOpponent(d.starting_player);
    // Everyone's back in for the new auction — clear last round's greyed-out state.
    Object.values(game.opponents).forEach((o) => { o.outOfAuction = false; });
    enqueueEvent(isSpectator, `New auction: ${describeCard(d.card)}`, 'start');
    logLine(`🃏 Auction #${d.round_number}: ${describeCard(d.card)}`, isSpectator);
  } else if (d.kind === 'turn_start') {
    game.turnPlayer = d.player;
    if (d.player !== game.myUsername) ensureOpponent(d.player);
  } else if (d.kind === 'bid') {
    if (d.player === game.myUsername) {
      game.myAuctionBid = d.max_bid; // this event's max_bid is the bidder's own new cumulative total
      updateBidStatus();
    } else {
      ensureOpponent(d.player);
    }
    enqueueEvent(isSpectator, `${actorLabel(d.player)} raised to ${d.max_bid}`, 'bid');
    logLine(`💰 ${d.player} raised to ${d.max_bid}`, isSpectator);
  } else if (d.kind === 'pass' || d.kind === 'fold') {
    if (d.player !== game.myUsername) ensureOpponent(d.player).outOfAuction = true;
    enqueueEvent(isSpectator, `${actorLabel(d.player)} passed`, 'pass');
    logLine(`⚪ ${d.player} passed`, isSpectator);
  } else if (d.kind === 'quit') {
    if (d.player !== game.myUsername) {
      const o = ensureOpponent(d.player);
      o.active = false;
      o.outOfAuction = true;
    }
    enqueueEvent(isSpectator, `${actorLabel(d.player)} quit`, 'quit');
    logLine(`❌ ${d.player} quit`, isSpectator);
  } else if (d.kind === 'sync') {
    // Reconnect catch-up only (see web_server.py's _send_reconnect_catchup)
    // — a silent state restore, not a live event: no toast, no log line,
    // and deliberately doesn't touch myAuctionBid/outOfAuction the way a
    // real auction_start does, since we don't know whether we'd already
    // committed part of a bid before dropping.
    game.turnPlayer = d.turn_player;
    if (d.turn_player && d.turn_player !== game.myUsername) ensureOpponent(d.turn_player);
  }

  renderAuctionPanel(isSpectator);
}

function applyAuctionResult(msg, isSpectator) {
  const d = msg.data;
  if (d.recipient) {
    if (d.recipient !== game.myUsername) {
      ensureOpponent(d.recipient).statusCards.push(d.card);
    }
    const spent = (d.money_spent && d.money_spent[d.recipient]) || 0;
    if (d.auction_type === 'disgrace') {
      // DISGRACE_ASSIGNED: recipient here is whoever passed first and got
      // stuck with the card, not someone who "bought" anything.
      const isMe = d.recipient === game.myUsername;
      enqueueEvent(isSpectator, `${actorLabel(d.recipient)} ${isMe ? 'are' : 'is'} stuck with ${describeCard(d.card)}!`, 'disgrace');
    } else {
      // BUY
      enqueueEvent(isSpectator, `${actorLabel(d.recipient)} bought ${describeCard(d.card)} for ${spent}`, 'buy');
    }
    logLine(`🏆 ${d.recipient} won ${describeCard(d.card)} for ${spent}`, isSpectator);
  } else {
    enqueueEvent(isSpectator, `Nobody wanted ${describeCard(d.card)}`, 'pass');
    logLine(`⚠️ Nobody took ${describeCard(d.card)}`, isSpectator);
  }
  renderOpponents(isSpectator);
}

function applyPlayerState(msg) {
  const d = msg.data;
  game.myPoints = d.points;
  game.myStatusCards = d.status_cards;
  renderMyPanel();
  // The server sends this at game start and after every action that
  // changes this player's money (their own bid/pass/fold, or a disgrace
  // auction's settlement) — see gameplay.py's _send_player_state call sites
  // — specifically so the money-card panel doesn't go stale between this
  // player's own turns. It used to only ever get rebuilt by the live
  // PLAYER_MOVE prompt (their actual turn), so everyone else's actions in
  // between left it showing whatever was true as of this player's *last*
  // turn — e.g. still greyed-out and missing money that had since been
  // refunded from a pass/fold. Reusing renderMoneyChips (rather than a
  // separate read-only copy) is safe even while the panel is pending/
  // greyed: .move-panel.pending's pointer-events:none already blocks any
  // click on the buttons this rebuilds.
  if (Array.isArray(d.money_cards)) {
    renderMoneyChips(d.money_cards);
    // First call ever (game just started, before this player's first
    // turn) — show the panel now, in its usual "not your turn" greyed
    // state, instead of leaving it hidden until their first real prompt.
    const movePanel = $('move-panel');
    if (movePanel.classList.contains('hidden')) {
      movePanel.classList.remove('hidden');
      movePanel.classList.add('pending');
    }
  }
}

function applyPlayerMove(msg) {
  $('move-panel').classList.remove('hidden', 'pending');
  const bidControls = $('bid-controls');
  const discardControls = $('discard-controls');
  if (msg.move_type === 'discard_painting') {
    // Discard has no bid-error concept at all, so it's safe (and correct) to
    // clear out any leftover bid-rejection error here rather than leaving it
    // visible under the now-irrelevant discard controls.
    hide($('move-error'));
    bidControls.classList.add('hidden');
    discardControls.classList.remove('hidden');
    renderPaintingChoices(msg.constraints.allowed_paintings);
    // Discard prompts never carry a per-move timer (see
    // NetworkPlayer.choose_painting_to_discard — it waits indefinitely), so
    // clear out any leftover countdown from this player's last bidding turn
    // rather than leaving a stale/wrong number showing.
    clearMoveTimer();
  } else {
    // Deliberately NOT clearing #move-error here: this same branch is what
    // renders the very next bid prompt immediately after a rejected bid (see
    // the INPUT_ERROR case above) — the server loops back and re-prompts
    // right away, far faster than a human can read the error. Leaving the
    // error up means a rejected bid stays visible until the player actually
    // does something new (onPlaceBid/onPass/onResign below each clear it
    // before sending), exactly matching the client-side "select at least
    // one money card" case.
    discardControls.classList.add('hidden');
    bidControls.classList.remove('hidden');
    game.selectedBid = new Set();
    renderMoneyChips(msg.constraints.allowed_money_cards);
    updateBidStatus();
  }
}

// Optional per-move countdown (host-configured "time per move" — see
// host-turn-time in the lobby form). The server sends one PLAYER_MOVE_TIMER
// message per move (or per retry after an invalid input) with the seconds
// remaining *at that instant*; it doesn't tick the value down itself, so
// this runs a local countdown from that starting point. Games hosted with
// no time limit simply never receive this message, so the element just
// never appears — the feature is a no-op unless a host opts in.
let moveTimerInterval = null;
let moveTimerDeadline = null;
// Whether the double-beep has already fired for the *current* move's urgent
// window — set once on the transition into "urgent", not per second, so it
// never repeats every tick (see updateMoveTimerDisplay).
let moveTimerUrgentAnnounced = false;

function startMoveTimer(secondsRemaining) {
  clearMoveTimer();
  moveTimerDeadline = Date.now() + secondsRemaining * 1000;
  updateMoveTimerDisplay();
  moveTimerInterval = setInterval(updateMoveTimerDisplay, 250);
}

function clearMoveTimer() {
  if (moveTimerInterval) { clearInterval(moveTimerInterval); moveTimerInterval = null; }
  moveTimerDeadline = null;
  moveTimerUrgentAnnounced = false;
  $('move-timer').classList.add('hidden');
}

// How many seconds before zero the clock should turn urgent — scaled to the
// room's actual per-move limit rather than a flat 5s, since 5s left out of
// a 20s move reads very differently than 5s left out of a 3-minute one.
function urgentWindowSeconds() {
  const limit = game && game.turnTimeLimit;
  if (!limit || limit < 30) return 5;
  if (limit <= 180) return 15; // >30s and up through 3 minutes
  return 30; // beyond 3 minutes
}

function updateMoveTimerDisplay() {
  const remaining = Math.max(0, (moveTimerDeadline - Date.now()) / 1000);
  const el = $('move-timer');
  const secondsLeft = Math.ceil(remaining);
  el.textContent = `⏰ ${secondsLeft}s left`;
  el.classList.remove('hidden');
  const isUrgent = remaining > 0 && remaining <= urgentWindowSeconds();
  el.classList.toggle('urgent', isUrgent);
  if (isUrgent && !moveTimerUrgentAnnounced) {
    moveTimerUrgentAnnounced = true;
    playUrgentDoubleBeep();
  }
  if (remaining <= 0) {
    // The server auto-passes on timeout (see gameplay.py's
    // _handle_player_turn), but its broadcast of that — and whatever
    // happens right after (bots can resolve their own turns near-
    // instantly) — takes a moment to arrive. Marking the panel pending
    // immediately, the same treatment a real submitted move already gets,
    // avoids a stretch where the clock reads 0 but the bid controls still
    // look live and clickable for a beat before the table visibly moves on.
    setMovePending();
  }
}

// A single low-pitched double-beep (no audio file needed — fits this app's
// zero-external-assets approach), played once right as the clock turns
// urgent — not a tick repeated every second, which read as nagging rather
// than a clear "heads up." Wrapped in try/catch since some browsers block
// audio before any user gesture has happened on the page — by the time a
// timer is running the player has already clicked Join/a bid button, but
// this stays silent-safe regardless.
let _timerBeepAudioCtx = null;
function playUrgentDoubleBeep() {
  try {
    _timerBeepAudioCtx = _timerBeepAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const ctx = _timerBeepAudioCtx;
    const beepAt = (startTime) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 220; // low pitch, deliberately not the shrill 880Hz tick this replaced
      gain.gain.setValueAtTime(0.2, startTime);
      gain.gain.exponentialRampToValueAtTime(0.001, startTime + 0.1);
      osc.connect(gain).connect(ctx.destination);
      osc.start(startTime);
      osc.stop(startTime + 0.1);
    };
    beepAt(ctx.currentTime);
    beepAt(ctx.currentTime + 0.15); // fast-tempo second beep
  } catch (e) {
    // Silently skip — the visual countdown already conveys urgency.
  }
}

// Marks the move panel as "acted on, waiting for the table" — greyed out and
// non-interactive but still visible (so you can see what you just did),
// rather than disappearing entirely between your turns.
function setMovePending() {
  clearMoveTimer(); // acted — no need to keep counting down what's already submitted
  $('move-panel').classList.add('pending');
  // The panel is now correctly blocked, but the server's broadcast of what
  // actually happens next (whose turn it really is) hasn't arrived yet --
  // without this, game.turnPlayer keeps pointing at whoever just acted
  // (often *this* player), so the auction header kept reading "Your turn"
  // and the wrong opponent stayed highlighted for however long that
  // round-trip took. Blank/neutral here is honest about "we don't know
  // yet"; stale is actively misleading. Most visible right as a per-move
  // timer hits 0 — the countdown draws the eye to exactly this moment, so
  // a leftover "Your turn" next to an already-greyed-out panel read as a
  // real bug ("timer still going, money cards look active") even though
  // the panel itself was already correctly blocked underneath.
  game.turnPlayer = null;
  renderAuctionPanel(false);
  renderOpponents(false);
}

// ------------------------------------------------------------- rendering --

function renderAuctionPanel(isSpectator) {
  const prefix = isSpectator ? 'spec-' : '';
  $(`${prefix}round-label`).innerHTML = game.round ? `<span class="suit-icon">🂠</span> Auction <strong>#${game.round}</strong>` : '';
  // No separate "whose turn" treatment here beyond this label — it already
  // has its own pulsing dot, and the auction panel otherwise represents
  // shared state (card, bid) that stays fully legible regardless of whose
  // turn it is, not something that dims/greys based on turn.
  const turnText = game.turnPlayer === game.myUsername ? 'Your turn' : `${escapeHtml(game.turnPlayer)}'s turn`;
  $(`${prefix}turn-label`).innerHTML = game.turnPlayer
    ? `<span class="turn-dot"></span>${turnText}`
    : '';

  const bidEl = $(`${prefix}max-bid`);
  const newBid = game.maxBid || 0;
  if (Number(bidEl.textContent) !== newBid) {
    bidEl.textContent = newBid;
    bidEl.classList.remove('bump');
    void bidEl.offsetWidth; // restart the animation even if it's already mid-play
    bidEl.classList.add('bump');
  }

  const cardContainer = $(`${prefix}auction-card`);
  cardContainer.innerHTML = '';
  if (game.card) cardContainer.appendChild(cardEl(game.card, true));
  renderOpponents(isSpectator);
  updateCardInfoButton(isSpectator);
}

// Bidding-rules text for the ⓘ button next to the auction card, keyed by
// card.type — Painting/PrestigeCard are normal auctions (highest bidder wins
// and pays), FauxPas/Passe/Scandale are "disgrace" auctions with the exact
// opposite dynamic (first player to PASS takes the card; everyone else's
// raised money is simply lost). This is the single most confusing rule for
// new players, hence spelling it out per card type rather than assuming it's
// obvious from the card's face value alone.
const CARD_INFO_TEXT = {
  Painting: 'Normal auction. Highest bidder wins and pays their bid. Worth its printed value in points.',
  PrestigeCard: 'Normal auction. Highest bidder wins and pays their bid. Doubles your entire final score — high stakes!',
  FauxPas: "Disgrace auction — opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Taking it means you must immediately discard a Painting you own (or your next one, if you don't have one yet).",
  Passe: 'Disgrace auction — opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Costs you 5 points.',
  Scandale: 'Disgrace auction — opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Halves your entire final score.',
};

// Keeps the ⓘ button (and its popover's contents) next to the auction card
// in sync with whatever's currently up for auction. Hidden entirely when
// there's no card up (e.g. between auctions) — the popover itself also gets
// force-closed at that point so it can't be left open showing stale text
// into the next auction. Reuses describeCard() so the popover's title always
// matches the label already shown elsewhere for this same card.
function updateCardInfoButton(isSpectator) {
  const prefix = isSpectator ? 'spec-' : '';
  const btn = $(`${prefix}card-info-btn`);
  const popover = $(`${prefix}card-info-popover`);
  const text = game.card && CARD_INFO_TEXT[game.card.type];
  if (!text) {
    btn.classList.add('hidden');
    popover.classList.add('hidden');
    return;
  }
  btn.classList.remove('hidden');
  $(`${prefix}card-info-title`).textContent = describeCard(game.card);
  $(`${prefix}card-info-text`).textContent = text;
}

// Updates existing row elements in place (keyed by data-username) instead of
// wiping and rebuilding the whole list every render. That's what lets the
// current-turn pulsing glow and the background/box-shadow transition on
// .opponent-row actually animate when the active player changes — a freshly
// recreated element has no "previous state" for a CSS transition to animate
// from, it just appears with its final style already applied.
// Puts the opponent list in real seat/turn order instead of whatever order
// each player was first heard about (which used to make turns look like
// they jumped around at random -- see the player_order GLOBAL_EVENT
// handler above for where game.playerOrder comes from). Rotated to start
// right after "me" for players, so the list always reads top-to-bottom in
// the exact order turns will actually advance, wrapping bottom-to-top --
// even when the shuffle put you mid-cycle rather than first. Spectators
// have no seat of their own to rotate around, so they just see the raw
// seat order start to finish.
function orderedOpponentUsernames(isSpectator) {
  const known = Object.keys(game.opponents);
  if (!game.playerOrder.length) return known; // player_order hasn't arrived yet -- fall back to insertion order

  let order = game.playerOrder;
  if (!isSpectator && game.myUsername) {
    const myIdx = order.indexOf(game.myUsername);
    if (myIdx !== -1) order = order.slice(myIdx + 1).concat(order.slice(0, myIdx));
  }
  const result = order.filter((u) => u !== game.myUsername && known.includes(u));
  // Defensive fallback only -- player_order is broadcast right at game
  // start, so this shouldn't normally trigger, but nobody should silently
  // vanish from the list if it somehow does.
  known.forEach((u) => { if (!result.includes(u)) result.push(u); });
  return result;
}

function renderOpponents(isSpectator) {
  if (!game) return;
  const container = $(isSpectator ? 'spec-players-list' : 'opponents-list');
  const seenUsernames = new Set();

  orderedOpponentUsernames(isSpectator).forEach((username) => {
    const o = game.opponents[username];
    if (!o) return;
    seenUsernames.add(username);
    let row = container.querySelector(`.opponent-row[data-username="${CSS.escape(username)}"]`);
    if (!row) {
      row = document.createElement('div');
      row.dataset.username = username;
      row.innerHTML = '<div class="opponent-header"><span class="name"></span><span class="pts"></span></div>'
        + '<div class="chip-row small"></div>';
    }
    // appendChild on an already-attached node moves it -- calling this
    // every render (not just on first creation) is what keeps existing
    // rows in the right order as game.playerOrder becomes known/changes,
    // not just newly-added ones.
    container.appendChild(row);

    const isCurrentTurn = game.turnPlayer === username;
    const classes = ['opponent-row'];
    if (o.active === false) classes.push('inactive');
    if (o.outOfAuction) classes.push('out-of-auction');
    else if (!isCurrentTurn) classes.push('waiting'); // still in this auction, just not acting right now
    if (isCurrentTurn) classes.push('current-turn');
    row.className = classes.join(' ');

    const ptsLabel = game.revealCards ? `Points: ${computePoints(o.statusCards)}` : `${o.statusCards.length} card${o.statusCards.length === 1 ? '' : 's'}`;
    // "(out)" (quit/disconnected — permanent) takes priority over
    // "(passed)" (just folded this one auction, still very much in the
    // game) — the dimmed/greyscale .out-of-auction styling alone wasn't a
    // clear enough signal on its own for what state a tile was actually in.
    let statusSuffix = '';
    if (o.active === false) statusSuffix = ' (out)';
    else if (o.outOfAuction) statusSuffix = ' (passed)';
    row.querySelector('.name').textContent = `${o.name}${statusSuffix}`;
    row.querySelector('.pts').textContent = ptsLabel;

    const chips = row.querySelector('.chip-row');
    chips.innerHTML = '';
    o.statusCards.forEach((c) => chips.appendChild(game.revealCards ? cardEl(c) : cardBackEl()));
  });

  container.querySelectorAll('.opponent-row').forEach((row) => {
    if (!seenUsernames.has(row.dataset.username)) row.remove();
  });
}

function renderMyPanel() {
  $('my-username-label').textContent = game.myUsername || '';
  $('my-points').textContent = game.myPoints;
  const chips = $('my-status-cards');
  chips.innerHTML = '';
  game.myStatusCards.forEach((c) => chips.appendChild(cardEl(c)));
}

function renderMoneyChips(values) {
  const row = $('my-money-cards');
  row.innerHTML = '';
  values.slice().sort((a, b) => a - b).forEach((value) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip money';
    btn.textContent = value;
    btn.addEventListener('click', () => {
      if (game.selectedBid.has(value)) game.selectedBid.delete(value); else game.selectedBid.add(value);
      btn.classList.toggle('selected');
      updateSelectedBidTotal();
    });
    row.appendChild(btn);
  });
  updateSelectedBidTotal();
}

// Money committed to an auction stays on the table for its whole duration
// (BasePlayer.place_bid() adds to current_bid_value across turns, it never
// replaces it) — so "selected" chips here are cards being ADDED on top of
// whatever you already committed earlier this same auction, not your new
// total outright. Surfacing both numbers is what point 3 of the user's
// feedback asked for: it's otherwise hard to tell how much more you need
// without digging through the log.
function updateBidStatus() {
  $('my-current-bid').textContent = game.myAuctionBid;
  $('bid-need-more').textContent = game.maxBid > 0 ? `(add more than ${game.maxBid - game.myAuctionBid} to raise)` : '';
  updateSelectedBidTotal();
}

function updateSelectedBidTotal() {
  const addingTotal = [...game.selectedBid].reduce((a, b) => a + b, 0);
  $('selected-bid').textContent = addingTotal;
  $('new-total-bid').textContent = game.myAuctionBid + addingTotal;
}

// Selecting a painting doesn't discard it immediately — a Faux Pas is
// irreversible, and a bare click (unlike a bid, which shows its own
// running total before submission) gave a hand slip no chance to be
// noticed before it was already sent. Bots are unaffected: this is purely
// this UI's own two-step confirmation on top of the same RESPONSE message
// a single click always sent — the engine still just sees one answer.
let selectedDiscardValue = null;

function renderPaintingChoices(values) {
  const row = $('my-paintings');
  row.innerHTML = '';
  selectedDiscardValue = null;
  $('btn-discard-painting').disabled = true;
  values.forEach((value) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip neutral';
    btn.textContent = value;
    btn.addEventListener('click', () => {
      selectedDiscardValue = value;
      row.querySelectorAll('button.chip').forEach((b) => b.classList.remove('selected'));
      btn.classList.add('selected');
      $('btn-discard-painting').disabled = false;
    });
    row.appendChild(btn);
  });
}

function onDiscardPainting() {
  if (selectedDiscardValue === null) return;
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: String(selectedDiscardValue) }));
  setMovePending();
}

// ------------------------------------------------------------- controls --

function onPlaceBid() {
  hide($('move-error'));
  const values = [...game.selectedBid];
  if (values.length === 0) { showError($('move-error'), 'Select at least one money card.'); return; }
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: JSON.stringify(values) }));
  // Once sent, these chips are no longer "being added on top" — they're
  // already part of the committed bid. Without clearing this, the server's
  // own echo of this same bid (applyAuctionUpdate's "bid" kind, which
  // updates game.myAuctionBid to the new committed total and re-renders)
  // would add the just-submitted chips a *second* time on top of that new
  // total, e.g. selecting 10 shows "10 → 20" instead of "0 → 10".
  game.selectedBid.clear();
  updateSelectedBidTotal();
  setMovePending();
}

function onPass() {
  hide($('move-error'));
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: 'pass' }));
  setMovePending();
}

async function onResign() {
  const ok = await confirmDialog('Are you sure you want to resign?', 'Resign');
  if (!ok) return;
  hide($('move-error'));
  hasResigned = true;
  clearRejoinInfo(currentRoomCode);
  // A dedicated out-of-band message, not a RESPONSE to whatever prompt
  // happens to be live -- resigning needs to work regardless of whose turn
  // it is (see WebSocketTransport's RESIGN handling and web_server.py's
  // on_resign), unlike a bid/pass/discard answer.
  ws.send(JSON.stringify({ message_type: 'RESIGN' }));
  setMovePending();
  $('btn-resign').disabled = true; // already resigned -- nothing left to submit twice
}
