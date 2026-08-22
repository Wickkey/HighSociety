// IDENTIFY handshake state + top-level message dispatch for both the player
// and spectator WebSocket connections.
//
// Circular imports with websocket.js/lobby.js/playerList.js/rematch.js are
// expected and safe: every one of these is only ever touched inside a
// function body (never at this module's own top-level evaluation), by
// which point every module involved has finished loading.
import { $, showError, showScreen } from '../utils/dom.js';
import { setBadge } from '../auth/profile.js';
import { game } from '../game/gameState.js';
import { applyGameMessage, ensureGameScreenVisible } from '../game/gameEvents.js';
import { ws } from './websocket.js';
import { currentRoomCode, clearRejoinInfo, saveRejoinInfo } from '../lobby/lobby.js';
import { startWaitingRoomPolling } from '../lobby/playerList.js';
import { handleRematchMessage } from '../lobby/rematch.js';

// pendingJoin/pendingSpectate: the {username, name} this connection is
// identifying as, set right before opening the socket (see lobby.js's
// onJoin/onSpectateJoin) and read the moment the server's IDENTIFY prompt
// arrives (respondIdentify below).
let pendingJoin = null;
let pendingSpectate = null;
export function setPendingJoin(v) { pendingJoin = v; }
export function setPendingSpectate(v) { pendingSpectate = v; }

// Set by a failed join's IDENTIFY_ERROR, read once by lobby.js's renderLobby
// the next time it renders (then cleared) so the error survives the status
// poll landing before the join screen re-renders.
let pendingIdentifyError = null;
export function takePendingIdentifyError() {
  const v = pendingIdentifyError;
  pendingIdentifyError = null;
  return v;
}

// isReconnecting distinguishes "this IDENTIFY_SUCCESS is a fresh join" vs
// "this is resuming an existing seat" below; reconnectAttempted (in
// lobby.js's renderForStatus) guards against retrying a bad/expired token
// in a loop.
let isReconnecting = false;
export function beginReconnectAttempt() { isReconnecting = true; }

export function respondIdentify(socket, pending, msg) {
  const wantsUsername = /username/i.test(msg.prompt);
  const answer = wantsUsername ? pending.username : pending.name;
  socket.send(JSON.stringify({ message_type: 'IDENTIFY_ACK', prompt: answer }));
}

export function handlePlayerMessage(msg) {
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
        clearRejoinInfo(currentRoomCode());
        ws.close();
        showScreen('screen-join');
        $('join-form').classList.add('hidden');
        $('join-waiting').classList.add('hidden');
        $('lobby-status').textContent = msg.prompt || 'A game is already in progress. You can watch as a spectator.';
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
          saveRejoinInfo(currentRoomCode(), msg.data.rejoin_token, pendingJoin.username, pendingJoin.name);
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
    case 'REMATCH_DECLINED':
    case 'REMATCH_STARTING':
      handleRematchMessage(msg);
      break;
    default:
      applyGameMessage(msg, false);
  }
}

export function handleSpectatorMessage(msg) {
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
