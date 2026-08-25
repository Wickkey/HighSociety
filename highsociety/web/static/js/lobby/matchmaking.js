// The ELO matchmaking flow: find-match, live queue polling, elapsed timer,
// cancel, and the "fill with bots after timing out" fallback.
import { $, hide, show, showScreen, setScreenPath } from '../utils/dom.js';
import { loadProfile } from '../auth/profile.js';
import { fetchJSON, enterRoom, onJoin, startRoomsPolling, showHomeTiles } from './lobby.js';

let matchmakingTicketId = null;
let matchmakingPollTimer = null;
let matchmakingSeats = null;
let matchmakingStartedAt = null;
let matchmakingElapsedTimer = null;

export function onPlayClick() {
  showScreen('screen-matchmaking');
  setScreenPath('/play');
  show($('matchmaking-setup'));
  hide($('matchmaking-waiting'));
}

export async function onFindMatch() {
  const profile = loadProfile();
  if (!profile) { showScreen('screen-login'); return; } // defensive -- see ensureProfileSet
  const seats = parseInt($('matchmaking-seats').value, 10) || 3;
  matchmakingSeats = seats;
  hide($('matchmaking-setup'));
  hide($('matchmaking-timeout-options'));
  $('matchmaking-status-text').textContent = 'Finding you an opponent…';
  show($('matchmaking-waiting'));
  try {
    const result = await fetchJSON('/api/matchmaking/join', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: profile.username, seats }),
    });
    matchmakingTicketId = result.ticket_id;
    startMatchmakingPolling();
    startMatchmakingElapsedTimer();
  } catch (e) {
    $('matchmaking-status-text').textContent = e.message;
  }
}

function startMatchmakingPolling() {
  stopMatchmakingPolling();
  pollMatchmakingStatus();
  matchmakingPollTimer = setInterval(pollMatchmakingStatus, 1200);
}

function stopMatchmakingPolling() {
  if (matchmakingPollTimer) { clearInterval(matchmakingPollTimer); matchmakingPollTimer = null; }
}

// A visible "how long has this been going" counter (chess.com/Rocket
// League-style) -- separate from the status poll interval since it ticks
// every second purely client-side, no need to round-trip to the server
// just to update a clock.
function startMatchmakingElapsedTimer() {
  matchmakingStartedAt = Date.now();
  updateMatchmakingElapsed();
  stopMatchmakingElapsedTimer();
  matchmakingElapsedTimer = setInterval(updateMatchmakingElapsed, 1000);
}

function stopMatchmakingElapsedTimer() {
  if (matchmakingElapsedTimer) { clearInterval(matchmakingElapsedTimer); matchmakingElapsedTimer = null; }
}

function updateMatchmakingElapsed() {
  const totalSeconds = Math.floor((Date.now() - matchmakingStartedAt) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  $('matchmaking-elapsed').textContent = `${minutes}:${String(seconds).padStart(2, '0')}`;
}

async function pollMatchmakingStatus() {
  if (!matchmakingTicketId) return;
  let status;
  try {
    status = await fetchJSON(`/api/matchmaking/status?ticket=${encodeURIComponent(matchmakingTicketId)}`);
  } catch (e) {
    return; // transient network hiccup -- the next poll just tries again
  }
  if (status.matched) {
    stopMatchmakingPolling();
    matchmakingTicketId = null;
    $('matchmaking-status-text').textContent = 'Match found!';
    await enterJustMatchedRoom(status.room_code);
    return;
  }
  $('matchmaking-status-text').textContent = status.waiting_count > 1
    ? `Finding you an opponent… (${status.waiting_count} in queue)`
    : 'Finding you an opponent…';
  $('matchmaking-timeout-options').classList.toggle('hidden', !status.timed_out);
}

// Shared by both a real match and the "fill with bots" fallback below --
// enterRoom() (the same function a manual room-code join uses) lands on
// the join screen pre-filled with this browser's saved identity (see
// lobby.js's applyJoinIdentityDefaults/playerList.js's renderLobby), so an
// immediate onJoin() call completes the connection with no extra click,
// matching what a matchmade match should feel like. Guarded on actually
// landing on #screen-join in case the room's state has somehow already
// moved past "lobby" by the time this resolves.
async function enterJustMatchedRoom(roomCode) {
  await enterRoom(roomCode);
  if (!$('screen-join').classList.contains('hidden')) onJoin();
}

// Also called from showScreen() itself whenever navigation away from
// #screen-matchmaking happens through some *other* route (a sidebar
// click, the header title, browser back via a room-code URL, ...) --
// without this, an abandoned ticket would sit in the queue indefinitely,
// polling in the background from a screen the user can no longer see.
export function cancelMatchmakingTicketQuietly() {
  stopMatchmakingPolling();
  stopMatchmakingElapsedTimer();
  const ticketId = matchmakingTicketId;
  matchmakingTicketId = null;
  if (ticketId) {
    fetchJSON('/api/matchmaking/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket_id: ticketId }),
    }).catch(() => {}); // best-effort -- worst case the ticket just sits unmatched
  }
}

export function onMatchmakingCancel() {
  cancelMatchmakingTicketQuietly();
  showScreen('screen-host-setup');
  showHomeTiles();
  startRoomsPolling();
}

// Reuses the normal host-a-game path (/api/create_game) rather than
// inventing a second way to seat bots -- "medium" matches the web
// lobby's own default difficulty for the waiting-room's "Add a bot"
// picker, since this screen has no UI of its own to choose one.
export async function onMatchmakingAddBots() {
  cancelMatchmakingTicketQuietly();
  const seats = matchmakingSeats || 3;
  try {
    const room = await fetchJSON('/api/create_game', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seats, bot_mix: Array(seats - 1).fill('medium'), visibility: 'private' }),
    });
    await enterJustMatchedRoom(room.room_code);
  } catch (e) {
    $('matchmaking-status-text').textContent = e.message;
  }
}
