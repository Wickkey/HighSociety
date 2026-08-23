// The waiting-room roster (before a game starts) and "add a bot" control.
//
// Circular with network/messages.js (which imports startWaitingRoomPolling
// from here) -- safe, same reasoning as lobby.js's own note: everything
// here is read inside a function body, never at module-evaluation time.
import { $, hide, show, showError, showScreen } from '../utils/dom.js';
import { fetchJSON, currentRoomCode, applyJoinIdentityDefaults } from './lobby.js';
import { takePendingIdentifyError } from '../network/messages.js';

export function renderLobby(status) {
  showScreen('screen-join');
  $('join-form').classList.remove('hidden');
  $('join-waiting').classList.add('hidden');
  applyJoinIdentityDefaults();
  const visibilityNote = status.visibility === 'private' ? ' (private, share this code with friends)' : ' (public)';
  $('room-code-text').textContent = `Room code: ${status.room_code}${visibilityNote}`;
  show($('room-code-display'));
  $('room-link-input').value = `${location.origin}${location.pathname}?room=${encodeURIComponent(status.room_code)}`;
  show($('room-link-row'));
  const names = status.joined.map((p) => `${p.name}${p.is_bot ? ' 🤖' : ''}`).join(', ') || 'nobody yet';
  $('lobby-status').textContent = `Seats filled: ${status.joined.length}/${status.seats} (${names})`;
  const err = takePendingIdentifyError();
  if (err) showError($('join-error'), err);
}

// Separate from the main status poll (which onJoin() stops the moment you
// connect — see lobby.js's connectPlayerSocket) because "waiting in the
// lobby after joining" is a distinct phase: nothing about the shared game/
// opponents state has started yet, but you still want to see the seat
// count update live as other people join, and to know when there's still
// an empty seat worth filling with a bot (see onAddBot). Self-cancels once
// the room leaves "lobby" (game started), so it never runs for the rest of
// the game.
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
  const names = status.joined.map((p) => `${p.name}${p.is_bot ? ' 🤖' : ''}`).join(', ') || 'nobody yet';
  $('lobby-status').textContent = `Seats filled: ${status.joined.length}/${status.seats} (${names})`;
}

export function startWaitingRoomPolling() {
  if (waitingRoomPollTimer) return;
  // Without this immediate call, setInterval's first tick is 1.5s out --
  // "Seats filled: 0/3 — nobody yet" (whatever lobby-status showed before
  // you joined) sits directly next to "You're in!" for that whole window,
  // visibly contradicting it. Matches lobby.js's startRoomsPolling same fix.
  _tickWaitingRoomStatus();
  waitingRoomPollTimer = setInterval(_tickWaitingRoomStatus, 1500);
}
export function stopWaitingRoomPolling() {
  if (waitingRoomPollTimer) { clearInterval(waitingRoomPollTimer); waitingRoomPollTimer = null; }
}

export async function onAddBot() {
  hide($('add-bot-error'));
  const botType = $('waiting-bot-type').value;
  try {
    await fetchJSON('/api/add_bot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room: currentRoomCode(), bot_type: botType }),
    });
    // The waiting-room poll (already running — see startWaitingRoomPolling)
    // picks up the new seat count on its own next tick; if this bot filled
    // the last seat, the game itself starts server-side and this player's
    // already-open WebSocket starts receiving real game messages naturally.
  } catch (e) {
    showError($('add-bot-error'), e.message);
  }
}
