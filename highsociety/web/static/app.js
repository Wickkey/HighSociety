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
function showError(el, text) { el.textContent = text; el.classList.remove('hidden'); }

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
  badge.textContent = text;
  badge.classList.remove('hidden');
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

function enqueueEvent(isSpectator, text, tone) {
  const key = isSpectator ? 'spec' : 'game';
  eventQueue[key].push({ text, tone });
  pumpEventQueue(key);
}

function pumpEventQueue(key) {
  if (eventQueueBusy[key] || eventQueue[key].length === 0) return;
  eventQueueBusy[key] = true;
  const { text, tone } = eventQueue[key].shift();
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
  }, TOAST_DURATION_MS);
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
function showFinalGreenOverlay(isSpectator, count) {
  const overlay = $(isSpectator ? 'spec-final-green-overlay' : 'final-green-overlay');
  overlay.querySelector('.final-green-title').textContent = `${ordinal(count)} Green Card Revealed!`;
  overlay.classList.remove('show');
  void overlay.offsetWidth; // restart the entrance animation even on a rapid repeat
  overlay.classList.add('show');
  clearTimeout(overlay._hideTimer);
  overlay._hideTimer = setTimeout(() => overlay.classList.remove('show'), 4000);
}

// The pre-game "starting in N..." countdown gets the same unmissable-
// overlay treatment as the game-ending green card, so the moment a lobby
// actually becomes a live game reads as an event, not a buried log line.
// Each tick updates the same banner in place (rather than re-popping fresh
// every second) so it reads as one smooth countdown; only pops in once, on
// the first tick.
function showCountdownOverlay(isSpectator, secondsLeft) {
  const overlay = $(isSpectator ? 'spec-game-start-overlay' : 'game-start-overlay');
  const alreadyShowing = overlay.classList.contains('show');
  overlay.querySelector('.game-start-icon').textContent = '⏳';
  overlay.querySelector('.game-start-title').textContent = `Game starting in ${secondsLeft}…`;
  overlay.querySelector('.game-start-sub').textContent = 'Get ready!';
  clearTimeout(overlay._hideTimer);
  if (!alreadyShowing) {
    overlay.classList.remove('show');
    void overlay.offsetWidth;
    overlay.classList.add('show');
  }
}

// The countdown's final tick — a shorter linger than the countdown itself
// since real gameplay (the first auction) starts immediately after.
function showGameStartedOverlay(isSpectator) {
  const overlay = $(isSpectator ? 'spec-game-start-overlay' : 'game-start-overlay');
  overlay.querySelector('.game-start-icon').textContent = '🚀';
  overlay.querySelector('.game-start-title').textContent = 'Game Started!';
  overlay.querySelector('.game-start-sub').textContent = 'Good luck!';
  overlay.classList.add('show');
  clearTimeout(overlay._hideTimer);
  overlay._hideTimer = setTimeout(() => overlay.classList.remove('show'), 1500);
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
    revealCards: status ? status.reveal_cards !== false : true,
    showLogs: status ? status.show_logs !== false : true,
  };
  applyRoomDisplaySettings();
}

// Reflects the room's fixed reveal-cards/show-logs settings in the UI: a
// read-only status label (there's no toggle anymore — see resetGameState)
// and hiding the log panel entirely when the host turned it off.
function applyRoomDisplaySettings() {
  const label = game.revealCards ? 'Cards revealed' : 'Cards hidden';
  $('reveal-cards-status').textContent = label;
  $('spec-reveal-cards-status').textContent = label;
  $('game-log').closest('details').classList.toggle('hidden', !game.showLogs);
  $('spec-game-log').closest('details').classList.toggle('hidden', !game.showLogs);
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
  currentRoomCode = new URLSearchParams(location.search).get('room');
  if (currentRoomCode) {
    refreshStatus();
    startPolling();
  } else {
    showScreen('screen-host-setup');
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
async function enterRoom(roomCode) {
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
  currentRoomCode = roomCode;
  history.replaceState(null, '', `?room=${encodeURIComponent(roomCode)}`);
  stopRoomsPolling();
  lastStatus = status;
  renderForStatus(status);
  startPolling();
}

function leaveToHome() {
  clearRejoinInfo(currentRoomCode);
  currentRoomCode = null;
  reconnectAttempted = false;
  history.replaceState(null, '', location.pathname);
  stopPolling();
  showScreen('screen-host-setup');
  startRoomsPolling();
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
    btn.addEventListener('click', () => enterRoom(r.room_code));
    row.appendChild(label);
    row.appendChild(btn);
    container.appendChild(row);
  });
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
    if (!reconnectAttempted) {
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
    $('lobby-status').textContent = 'A game is already in progress — you can watch as a spectator.';
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

function startWaitingRoomPolling() {
  if (waitingRoomPollTimer) return;
  waitingRoomPollTimer = setInterval(async () => {
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
  }, 1500);
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
  const visibilityNote = status.visibility === 'private' ? ' (private — share this code with friends)' : ' (public)';
  $('room-code-display').textContent = `Room code: ${status.room_code}${visibilityNote}`;
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
}

// ------------------------------------------------------------- host flow --

function wireStaticHandlers() {
  $('btn-create-game').addEventListener('click', onCreateGame);
  $('btn-join-by-code').addEventListener('click', onJoinByCode);
  $('btn-add-bot').addEventListener('click', onAddBot);
  $('btn-join').addEventListener('click', onJoin);
  $('btn-spectate-link').addEventListener('click', () => showScreen('screen-spectate-join'));
  $('btn-back-to-join').addEventListener('click', () => { showScreen('screen-join'); refreshStatus(); });
  $('btn-spectate-join').addEventListener('click', onSpectateJoin);
  $('btn-new-game').addEventListener('click', leaveToHome);
  $('btn-place-bid').addEventListener('click', onPlaceBid);
  $('btn-pass').addEventListener('click', onPass);
  $('btn-quit').addEventListener('click', onQuit);
  $('btn-spec-chat-send').addEventListener('click', onSpecChatSend);
  $('spec-chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') onSpecChatSend(); });
  $('spec-chat-target-toggle').addEventListener('change', (e) => {
    $('spec-chat-input').placeholder = e.target.checked ? 'Message spectators only…' : 'Message everyone…';
  });
  $('btn-player-chat-send').addEventListener('click', onPlayerChatSend);
  $('player-chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') onPlayerChatSend(); });

  window.addEventListener('beforeunload', (e) => {
    if (ws && ws.readyState === WebSocket.OPEN && game && game.round > 0) {
      e.preventDefault();
      e.returnValue = 'Leaving now drops you from the game — there is no reconnect.';
    }
  });
}

async function onCreateGame() {
  hide($('host-error'));
  const seats = parseInt($('host-seats').value, 10);
  const counts = {
    pass: parseInt($('bot-pass').value || '0', 10),
    greedy: parseInt($('bot-greedy').value || '0', 10),
    capped: parseInt($('bot-capped').value || '0', 10),
  };
  const botMix = [];
  for (const [type, n] of Object.entries(counts)) for (let i = 0; i < n; i += 1) botMix.push(type);

  const seedRaw = $('host-seed').value;
  const turnTimeRaw = $('host-turn-time').value;
  const body = {
    seats,
    bot_mix: botMix,
    seed: seedRaw ? parseInt(seedRaw, 10) : null,
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
    currentRoomCode = status.room_code;
    history.replaceState(null, '', `?room=${encodeURIComponent(status.room_code)}`);
    lastStatus = status;
    renderLobby(status);
    startPolling();
  } catch (e) {
    showError($('host-error'), e.message);
  }
}

function onJoinByCode() {
  hide($('host-error'));
  const code = $('join-room-code').value.trim().toUpperCase();
  if (!code) { showError($('host-error'), 'Enter a room code.'); return; }
  enterRoom(code);
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
        // Token was invalid/expired (e.g. the game finished in the
        // meantime, or someone else already reconnected with it) — drop it
        // and fall back to the normal "already in progress" message rather
        // than retrying it forever.
        isReconnecting = false;
        clearRejoinInfo(currentRoomCode);
        ws.close();
        showScreen('screen-join');
        $('join-form').classList.add('hidden');
        $('join-waiting').classList.add('hidden');
        $('lobby-status').textContent = 'A game is already in progress — you can watch as a spectator.';
      } else {
        pendingIdentifyError = msg.prompt;
        ws.close();
      }
      break;
    case 'IDENTIFY_SUCCESS':
      if (isReconnecting) {
        isReconnecting = false;
        setBadge(`playing as ${game.myUsername}`);
        ensureGameScreenVisible(false);
      } else {
        $('join-form').classList.add('hidden');
        $('join-waiting').classList.remove('hidden');
        setBadge(`playing as ${game.myUsername}`);
        startWaitingRoomPolling();
        if (msg.data && msg.data.rejoin_token) {
          saveRejoinInfo(currentRoomCode, msg.data.rejoin_token, pendingJoin.username, pendingJoin.name);
        }
      }
      break;
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
      } else if (d && d.event === 'countdown') {
        showCountdownOverlay(isSpectator, d.seconds_left);
      } else if (d && d.event === 'countdown_finished') {
        showGameStartedOverlay(isSpectator);
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
    // does something new (onPlaceBid/onPass/onQuit below each clear it
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
// Tracks the last whole-second value we've already beeped for, so the tick
// fires once per second during the urgent window instead of every 250ms
// poll (see updateMoveTimerDisplay).
let moveTimerLastBeepSecond = null;

function startMoveTimer(secondsRemaining) {
  clearMoveTimer();
  moveTimerDeadline = Date.now() + secondsRemaining * 1000;
  updateMoveTimerDisplay();
  moveTimerInterval = setInterval(updateMoveTimerDisplay, 250);
}

function clearMoveTimer() {
  if (moveTimerInterval) { clearInterval(moveTimerInterval); moveTimerInterval = null; }
  moveTimerDeadline = null;
  moveTimerLastBeepSecond = null;
  $('move-timer').classList.add('hidden');
}

function updateMoveTimerDisplay() {
  const remaining = Math.max(0, (moveTimerDeadline - Date.now()) / 1000);
  const el = $('move-timer');
  const secondsLeft = Math.ceil(remaining);
  el.textContent = `⏰ ${secondsLeft}s left`;
  el.classList.remove('hidden');
  const isUrgent = remaining > 0 && remaining <= 5;
  el.classList.toggle('urgent', isUrgent);
  if (isUrgent && secondsLeft !== moveTimerLastBeepSecond) {
    moveTimerLastBeepSecond = secondsLeft;
    playTimerTick();
  }
  if (remaining <= 0) clearMoveTimer();
}

// A short synthesized "tick" (no audio file needed — fits this app's
// zero-external-assets approach) played once per second while the move
// timer is in its urgent (<=5s) state, chess.com-clock-style. Wrapped in
// try/catch since some browsers block audio before any user gesture has
// happened on the page — by the time a timer is running the player has
// already clicked Join/a bid button, but this stays silent-safe regardless.
let _timerTickAudioCtx = null;
function playTimerTick() {
  try {
    _timerTickAudioCtx = _timerTickAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const ctx = _timerTickAudioCtx;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.12);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.12);
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
}

// ------------------------------------------------------------- rendering --

function renderAuctionPanel(isSpectator) {
  const prefix = isSpectator ? 'spec-' : '';
  $(`${prefix}round-label`).innerHTML = game.round ? `<span class="suit-icon">🂠</span> Auction <strong>#${game.round}</strong>` : '';
  // No separate "whose turn" treatment here beyond this label — it already
  // has its own pulsing dot, and the auction panel otherwise represents
  // shared state (card, bid) that stays fully legible regardless of whose
  // turn it is, not something that dims/greys based on turn.
  $(`${prefix}turn-label`).innerHTML = game.turnPlayer
    ? `<span class="turn-dot"></span>${escapeHtml(game.turnPlayer)}'s turn`
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
function renderOpponents(isSpectator) {
  if (!game) return;
  const container = $(isSpectator ? 'spec-players-list' : 'opponents-list');
  const seenUsernames = new Set();

  Object.entries(game.opponents).forEach(([username, o]) => {
    seenUsernames.add(username);
    let row = container.querySelector(`.opponent-row[data-username="${CSS.escape(username)}"]`);
    if (!row) {
      row = document.createElement('div');
      row.dataset.username = username;
      row.innerHTML = '<div class="opponent-header"><span class="name"></span><span class="pts"></span></div>'
        + '<div class="chip-row small"></div>';
      container.appendChild(row);
    }

    const isCurrentTurn = game.turnPlayer === username;
    const classes = ['opponent-row'];
    if (o.active === false) classes.push('inactive');
    if (o.outOfAuction) classes.push('out-of-auction');
    else if (!isCurrentTurn) classes.push('waiting'); // still in this auction, just not acting right now
    if (isCurrentTurn) classes.push('current-turn');
    row.className = classes.join(' ');

    const ptsLabel = game.revealCards ? `Points: ${computePoints(o.statusCards)}` : `${o.statusCards.length} card${o.statusCards.length === 1 ? '' : 's'}`;
    row.querySelector('.name').textContent = `${o.name}${o.active === false ? ' (out)' : ''}`;
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

function renderPaintingChoices(values) {
  const row = $('my-paintings');
  row.innerHTML = '';
  values.forEach((value) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip neutral';
    btn.textContent = value;
    btn.addEventListener('click', () => {
      ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: String(value) }));
      setMovePending();
    });
    row.appendChild(btn);
  });
}

// ------------------------------------------------------------- controls --

function onPlaceBid() {
  hide($('move-error'));
  const values = [...game.selectedBid];
  if (values.length === 0) { showError($('move-error'), 'Select at least one money card.'); return; }
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: JSON.stringify(values) }));
  setMovePending();
}

function onPass() {
  hide($('move-error'));
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: 'pass' }));
  setMovePending();
}

function onQuit() {
  if (!confirm('Quit the game? This cannot be undone.')) return;
  hide($('move-error'));
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: 'quit' }));
  setMovePending();
}
