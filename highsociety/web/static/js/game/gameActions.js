// User-initiated actions from the live game screen: bid, pass, discard,
// resign, quick reactions.
import { $, hide, showError } from '../utils/dom.js';
import { ws } from '../network/websocket.js';
import { game, answerMyPrompt } from './gameState.js';
import { clearMoveTimer, renderMovePanel, updateSelectedBidTotal, renderOpponents } from './gameRenderer.js';
import { confirmDialog } from '../ui/modals.js';
import { clearRejoinInfo, currentRoomCode, setHasResigned } from '../lobby/lobby.js';
import { showReactionBubble } from './gameEvents.js';

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
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: String(selectedDiscardValue) }));
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
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: JSON.stringify(values) }));
  // Once sent, these chips are no longer "being added on top" — they're
  // already part of the committed bid. Without clearing this, the server's
  // own echo of this same bid (gameEvents.js's applyAuctionUpdate "bid"
  // kind, which updates game.myAuctionBid to the new committed total and
  // re-renders) would add the just-submitted chips a *second* time on top
  // of that new total, e.g. selecting 10 shows "10 → 20" instead of "0 → 10".
  game.selectedBid.clear();
  updateSelectedBidTotal();
  answerMyPrompt();
}

export function onPass() {
  if (!game.myPrompt || game.myPrompt.answered) return;
  hide($('move-error'));
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: 'pass' }));
  answerMyPrompt();
}

export async function onResign() {
  const ok = await confirmDialog('Are you sure you want to resign?', 'Resign');
  if (!ok) return;
  hide($('move-error'));
  setHasResigned(true);
  clearRejoinInfo(currentRoomCode());
  // A dedicated out-of-band message, not a RESPONSE to whatever prompt
  // happens to be live -- resigning needs to work regardless of whose turn
  // it is (see WebSocketTransport's RESIGN handling and web_server.py's
  // on_resign), unlike a bid/pass/discard answer.
  ws.send(JSON.stringify({ message_type: 'RESIGN' }));
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
