// User-initiated actions from the live game screen: bid, pass, discard,
// resign, quick reactions.
import { $, hide, showError } from '../utils/dom.js';
import { ws, attemptReconnect, closeSocket } from '../network/websocket.js';
import { game, answerMyPrompt } from './gameState.js';
import { clearMoveTimer, renderMovePanel, updateSelectedBidTotal, renderOpponents, clearSelectedBidVisual } from './gameRenderer.js';
import { confirmDialog } from '../ui/modals.js';
import { clearRejoinInfo, currentRoomCode, setHasResigned } from '../lobby/lobby.js';
import { showReactionBubble } from './gameEvents.js';

// ----------------------------------------------------- delivery watchdog --
//
// A WebSocket can report readyState === OPEN while actually being a
// "zombie" connection that no longer delivers anything (a dead TCP
// connection the browser hasn't detected yet, a brief network hiccup) --
// ws.send() does not throw in that case. Without this, a bid/pass/discard
// would commit the UI to "answered" (panel greyed, local timer cleared)
// the instant send() returned, even though the server never received
// anything -- it would then sit there, indistinguishable from a normal
// "waiting for other players" state, until the *entire* per-move timer
// elapsed server-side and forced an auto-pass. Live-reported symptom:
// "clicked Pass, panel greyed out, but nothing happened until the timer
// ran out."
//
// This tracks exactly one thing: "is the action we just sent still
// unconfirmed." Armed by sendPlayerAction() right after a send; disarmed
// by gameEvents.js the moment a genuine server confirmation arrives for
// it (a fresh AUCTION_UPDATE/PLAYER_MOVE, or an INPUT_ERROR proving the
// server at least received *something* from us). If it fires with nothing
// having disarmed it, the send most likely never reached the server.
const ACTION_WATCHDOG_MS = 4000; // comfortably under any real per-move timer
let watchdogTimer = null;

function isSocketOpen() {
  return !!ws && ws.readyState === WebSocket.OPEN;
}

function armActionWatchdog() {
  disarmActionWatchdog();
  watchdogTimer = setTimeout(onActionWatchdogFired, ACTION_WATCHDOG_MS);
}

// Exported so gameEvents.js's confirming message handlers can cancel this
// without gameActions.js needing to know anything about which specific
// message types count as confirmation -- that knowledge belongs with the
// code that already parses those messages, not duplicated here.
export function disarmActionWatchdog() {
  if (watchdogTimer) {
    clearTimeout(watchdogTimer);
    watchdogTimer = null;
  }
}

function onActionWatchdogFired() {
  watchdogTimer = null;
  if (!isSocketOpen()) {
    // The browser itself now agrees the connection is gone -- safe to
    // reconnect automatically instead of just complaining about it.
    showError($('move-error'), 'Connection lost — reconnecting…');
    closeSocket();
    attemptReconnect();
  } else {
    // Still claims OPEN: a genuine zombie connection, not one we can
    // safely tear down and retry unattended (it might just be a slow
    // server, not a dead socket) -- surfacing this honestly is still
    // strictly better than the silent "stuck until the real timer
    // expires" symptom this replaces.
    showError($('move-error'), 'Still waiting on the server… if this persists, refresh the page.');
  }
}

// The one place a player action is actually transmitted -- every handler
// below funnels through this instead of calling ws.send() directly, so
// the "is the connection actually usable" check can't be forgotten at a
// future call site the way the previous fire-and-forget ws.send() calls
// were.
//
// `confirmable` arms the watchdog above for actions that have a natural
// server confirmation to wait for (bid/pass/discard: a fresh
// AUCTION_UPDATE/PLAYER_MOVE/INPUT_ERROR always follows one that was
// genuinely received). RESIGN has no such signal -- the server only ever
// notifies *other* players that someone resigned (see web_server.py's
// on_resign), never the resigning player themselves -- so arming a
// watchdog for it would just be a guaranteed false "still waiting" a few
// seconds after every successful resign. Its one real failure mode (the
// message never reaching the server at all) is already covered by the
// readyState check below.
function sendPlayerAction(payload, { confirmable = true } = {}) {
  if (!isSocketOpen()) {
    showError($('move-error'), 'Connection lost — reconnecting…');
    closeSocket();
    attemptReconnect();
    return false;
  }
  ws.send(JSON.stringify(payload));
  if (confirmable) armActionWatchdog();
  return true;
}

// Selecting a painting doesn't discard it immediately — a Faux Pas is
// irreversible, and a bare click (unlike a bid, which shows its own
// running total before submission) gave a hand slip no chance to be
// noticed before it was already sent. Bots are unaffected: this is purely
// this UI's own two-step confirmation on top of the same RESPONSE message
// a single click always sent — the engine still just sees one answer.
let selectedDiscardValue = null;
export function setSelectedDiscardValue(value) { selectedDiscardValue = value; }
// Reset alongside every other per-game piece of state -- see
// gameState.js's resetGameState, which calls this.
export function resetSelectedDiscard() { selectedDiscardValue = null; }

export function onDiscardPainting() {
  if (!game.myPrompt || game.myPrompt.answered) return;
  if (selectedDiscardValue === null) return;
  if (!sendPlayerAction({ message_type: 'RESPONSE', prompt: String(selectedDiscardValue) })) return;
  answerMyPrompt();
}

export function onPlaceBid() {
  // A read-only check here (not answerMyPrompt() itself) -- the "select at
  // least one money card" validation below must be able to fail without
  // consuming the prompt, so the player can fix their selection and try
  // again. answerMyPrompt() only actually gets called once a bid is
  // genuinely about to be sent.
  if (!game.myPrompt || game.myPrompt.answered) return;
  hide($('move-error'));
  const values = [...game.selectedBid];
  if (values.length === 0) { showError($('move-error'), 'Select at least one money card.'); return; }
  if (!sendPlayerAction({ message_type: 'RESPONSE', prompt: JSON.stringify(values) })) return;
  // Once sent, these chips are no longer "being added on top" — they're
  // already part of the committed bid. Without clearing this, the server's
  // own echo of this same bid (gameEvents.js's applyAuctionUpdate "bid"
  // kind, which updates game.myAuctionBid to the new committed total and
  // re-renders) would add the just-submitted chips a *second* time on top
  // of that new total, e.g. selecting 10 shows "10 → 20" instead of "0 → 10".
  game.selectedBid.clear();
  clearSelectedBidVisual();
  updateSelectedBidTotal();
  answerMyPrompt();
}

export function onPass() {
  if (!game.myPrompt || game.myPrompt.answered) return;
  hide($('move-error'));
  if (!sendPlayerAction({ message_type: 'RESPONSE', prompt: 'pass' })) return;
  answerMyPrompt();
}

export async function onResign() {
  const ok = await confirmDialog('Are you sure you want to resign?', 'Resign');
  if (!ok) return;
  hide($('move-error'));
  // A dedicated out-of-band message, not a RESPONSE to whatever prompt
  // happens to be live -- resigning needs to work regardless of whose turn
  // it is (see WebSocketTransport's RESIGN handling and web_server.py's
  // on_resign), unlike a bid/pass/discard answer. Not confirmable (see
  // sendPlayerAction's own comment): the server never tells the resigning
  // player themselves it was received, only everyone else. Checked and
  // sent *before* committing any local state below -- if this fails, the
  // player hasn't actually left as far as the server's concerned, so
  // neither should this browser's own idea of things.
  if (!sendPlayerAction({ message_type: 'RESIGN' }, { confirmable: false })) return;
  setHasResigned(true);
  clearRejoinInfo(currentRoomCode());
  // Deliberately not answerMyPrompt(): resigning ends this player's
  // participation regardless of whether a prompt is currently open (unlike
  // a bid/pass/discard answer, which only ever closes one that is) -- force
  // the panel pending either way, since there's nothing left to ever answer.
  clearMoveTimer();
  game.myPrompt = null;
  game.turnPlayer = null;
  renderMovePanel();
  $('btn-resign').disabled = true; // already resigned -- nothing left to submit twice
}

// Quick reactions: a fixed set of 5 emoji, sent instantly on click (no text
// entry, no send button) and rendered as a transient bubble over the
// sender's own tile -- see web_server.py's _relay_player_chat, which relays
// REACTION the same live off-thread way as CHAT. The server only relays to
// *other* players/spectators (mirroring CHAT's "every other" reach), so the
// sender shows their own bubble locally instead of waiting on a round trip.
export function onQuickReactionClick(emoji) {
  if (!ws || !game) return;
  ws.send(JSON.stringify({ message_type: 'REACTION', emoji }));
  showReactionBubble(game.myUsername, emoji, false);
}
