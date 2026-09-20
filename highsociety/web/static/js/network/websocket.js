// Owns the one live WebSocket connection (`ws`). Named-export `let` bindings
// are live in ES modules -- every other module that imports `{ ws }` always
// sees the current value after this module reassigns it internally; nothing
// outside this file may reassign `ws` directly (that's not legal JS anyway
// for an imported binding), which is why `closeSocket()` exists below rather
// than callers doing `ws = null` themselves.
//
// Circular import note: lobby.js imports connectPlayerSocket/attemptReconnect/
// connectSpectatorSocket/closeSocket/ws from here, and this file imports
// currentRoomCode/lastStatus/loadRejoinInfo/refreshStatus from lobby.js. This
// is safe -- every one of these is only ever read inside a function body
// (never at module-evaluation time), by which point both modules have
// finished loading and every binding is populated.
import { setSessionStatus } from '../auth/profile.js';
import { resetGameState, seedOpponents } from '../game/gameState.js';
import { handlePlayerMessage, handleSpectatorMessage, beginReconnectAttempt } from './messages.js';
import { currentRoomCode, lastStatus, loadRejoinInfo, refreshStatus } from '../lobby/lobby.js';

export let ws = null;

export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${path}`;
}

export function closeSocket() {
  if (ws) { ws.close(); ws = null; }
}

export function connectPlayerSocket() {
  closeSocket(); // never open a second connection on top of an existing one -- see connectSpectatorSocket's identical guard for the real bug this fixes
  const socket = new WebSocket(wsUrl(`/ws?room=${encodeURIComponent(currentRoomCode())}`));
  ws = socket;
  socket.onmessage = (evt) => handlePlayerMessage(JSON.parse(evt.data));
  // Guarded by identity, not just "close happened": a socket that's already
  // been superseded (closeSocket() ran, or a newer connectPlayerSocket/
  // attemptReconnect call replaced `ws`) still fires its own close event
  // later, asynchronously — WebSocket events are always dispatched as a
  // separate task, never synchronously inside .close(). An unguarded
  // handler would null out whatever *new* socket `ws` points to by then and
  // fire a spurious refreshStatus() on top of it. See gameActions.js's
  // delivery watchdog for the concrete case this was found from.
  socket.onclose = () => { if (ws === socket) { ws = null; refreshStatus(); } };
  setSessionStatus('connecting');
}

// Called when the room turns out to already be starting/in_progress and we
// have no open socket (fresh page load after a refresh, or the tab was just
// sitting on some other screen when the game started) — see lobby.js's
// renderForStatus. Returns true if a stored rejoin token existed and a
// reconnect attempt was actually started (caller should stop polling and
// wait for the result), false if there was nothing to try (falls through
// to the normal "watch as a spectator" message).
export function attemptReconnect() {
  const info = loadRejoinInfo(currentRoomCode());
  if (!info) return false;

  closeSocket(); // same guard as connectPlayerSocket -- never leave a prior connection orphaned
  beginReconnectAttempt();
  resetGameState(info.username, lastStatus());
  if (lastStatus()) seedOpponents(lastStatus(), info.username);
  const socket = new WebSocket(wsUrl(
    `/ws?room=${encodeURIComponent(currentRoomCode())}&rejoin_token=${encodeURIComponent(info.token)}`,
  ));
  ws = socket;
  socket.onmessage = (evt) => handlePlayerMessage(JSON.parse(evt.data));
  // See connectPlayerSocket's identical guard for why this checks identity
  // rather than unconditionally nulling `ws`.
  socket.onclose = () => { if (ws === socket) { ws = null; refreshStatus(); } };
  setSessionStatus('reconnecting');
  return true;
}

export function connectSpectatorSocket() {
  // This used to be the real bug: an already-seated player switching to
  // "Watch as a spectator instead" (lobby.js's onSpectateJoin) landed
  // here with `ws` still pointing at their live player connection --
  // reassigning `ws` below without closing it first orphaned that old
  // connection (never explicitly closed, so the server never noticed and
  // never freed the seat) while silently opening a second, unrelated
  // spectator connection on top of it. Closing whatever's already open
  // first reuses the exact same server-side lobby-disconnect cleanup
  // onHomeLinkClick's closeSocket() call already relies on to free a
  // seat correctly.
  closeSocket();
  const socket = new WebSocket(wsUrl(`/ws_spectate?room=${encodeURIComponent(currentRoomCode())}`));
  ws = socket;
  socket.onmessage = (evt) => handleSpectatorMessage(JSON.parse(evt.data));
  // The server closes every spectator's connection right after the game
  // ends (see GameRoom.run_game in web_server.py) — same signal the player
  // side already uses (connectPlayerSocket) to notice the game finished and
  // switch to the results screen. This was previously a no-op here, so a
  // spectator's browser just sat on the live table forever after the game
  // actually ended, never showing results at all.
  // Identity-guarded for the same reason as connectPlayerSocket/
  // attemptReconnect above.
  socket.onclose = () => { if (ws === socket) { ws = null; refreshStatus(); } };
}
