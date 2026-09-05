// Server -> client event handling for the live game: turns AUCTION_UPDATE/
// AUCTION_RESULT/PLAYER_STATE/PLAYER_MOVE/GLOBAL_EVENT/etc. into game-state
// mutations and DOM updates.
import { $, show, hide, showScreen } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { game, ensureOpponent, openMyPrompt } from './gameState.js';
import {
  renderAuctionPanel, renderOpponents, renderMyPanel, renderMoneyChips,
  renderPaintingChoices, updateBidStatus, actorLabel, describeCard,
  clearMoveTimer, startMoveTimer, renderMovePanel,
} from './gameRenderer.js';
import { setSelectedDiscardValue, disarmActionWatchdog } from './gameActions.js';
import {
  enqueueEvent, showFinalGreenOverlay, showCountdownOverlay, hideCountdownOverlay, logLine,
} from '../ui/notifications.js';
import { appendChatLine } from '../ui/chat.js';
import { refreshStatus } from '../lobby/lobby.js';
import { revealSpectateLiveLayout } from '../lobby/playerList.js';

export function ensureGameScreenVisible(isSpectator) {
  const id = isSpectator ? 'screen-spectate' : 'screen-game';
  if ($(id).classList.contains('hidden')) showScreen(id);
  // A real game message is the same "this is actually live now" signal a
  // player's own screen implicitly relies on -- see index.html's own
  // comment on #spectate-lobby-wait for the bug this replaces. A no-op
  // once already showing the live layout.
  if (isSpectator) revealSpectateLiveLayout();
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

// Tracks when the final-green overlay last appeared, so the game_over
// handler below can hold off switching to the results screen until it's
// actually had time on screen — the game ending this way has nothing left
// to broadcast afterward (see gameplay.py's comment on this being
// deliberately unpaced), so game_over used to arrive right on its heels
// and cut the overlay off before a human could read it.
let finalGreenOverlayShownAt = null;

export function applyGameMessage(msg, isSpectator) {
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
          finalGreenOverlayShownAt = Date.now();
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
      // move_seq staleness check mirrors openMyPrompt's identical guard
      // for PLAYER_MOVE: the server resends a fresh PLAYER_MOVE_TIMER
      // every 5s while waiting (_receive_with_periodic_resync) -- a real,
      // live-reported bug was one of those crossing in flight with this
      // player's own pass, arriving just after answerMyPrompt() already
      // cleared the timer and re-starting it for a decision that's
      // already been answered ("I passed, but I could still see my timer
      // ticking while waiting for everyone else").
      const moveSeq = msg.data && msg.data.move_seq;
      const isStale = moveSeq != null && game.highestAnsweredMoveSeq != null && moveSeq <= game.highestAnsweredMoveSeq;
      if (!isSpectator && !isStale && msg.data && typeof msg.data.seconds_remaining === 'number') {
        startMoveTimer(msg.data.seconds_remaining);
      }
      break;
    case 'INPUT_ERROR':
      if (!isSpectator) {
        // Proves the server received *something* from us for this prompt
        // (even though it rejected it) -- just as much a confirmed
        // delivery as an accepted one, so the watchdog's job here is done.
        disarmActionWatchdog();
        showError_moveError(msg.prompt);
        // onPlaceBid() already marked game.myPrompt answered the instant a
        // bid was sent, before the server had actually validated it (e.g.
        // an insufficient raise). NetworkPlayer only ever sends INPUT_ERROR
        // from the same blocking get_bid()/choose_painting_to_discard() call
        // that's still re-prompting *this* player (a fresh PLAYER_MOVE with
        // a new move_seq follows right behind it -- see gameplay.py's
        // retry loop), so this genuinely is the same still-open decision,
        // not a new one: reopen it for another attempt rather than waiting
        // out that second round trip. game.turnPlayer needs no separate
        // correction here -- the turn label now derives directly from
        // game.myPrompt (see renderAuctionPanel), so there is nothing left
        // for it to disagree with.
        if (game.myPrompt) game.myPrompt.answered = false;
        renderMovePanel();
      }
      break;
    case 'CHAT':
      appendChatLine(isSpectator ? 'spec-chat-log' : 'player-chat-log', msg.prompt);
      break;
    case 'REACTION':
      if (msg.from_user && msg.data && msg.data.emoji) {
        showReactionBubble(msg.from_user, msg.data.emoji, isSpectator);
      }
      break;
    default:
      break; // GLOBAL_MOVE_INFO, PLAYER_INFO: superseded by the structured messages above
  }
}

function showError_moveError(text) {
  const el = $('move-error');
  el.textContent = text;
  el.classList.remove('hidden');
}

function applyAuctionUpdate(msg, isSpectator) {
  const d = msg.data;
  // Turns are strictly sequential (the game engine blocks on exactly one
  // player's decision at a time -- see gameplay.py's auction loop), so
  // seeing *any* new auction activity while we're still waiting to hear
  // back from our own last action necessarily means the server has moved
  // past that decision point: either it genuinely received what we sent
  // (the common case this disarms), or our move_seq is already stale, or
  // -- if the whole per-move timer already elapsed -- the server's own
  // auto-pass beat us to it, but that takes far longer than the watchdog
  // window this is racing, so it's moot either way. See gameActions.js's
  // own comment for the full picture of what this is guarding against.
  disarmActionWatchdog();
  // Persistent state updates immediately and unconditionally — it must
  // never lag behind or wait on the transient toast queue below, since it's
  // the actual shared game state, not decoration.
  game.round = d.round_number;
  game.card = d.card;
  if (typeof d.max_bid === 'number') game.maxBid = d.max_bid;

  // A player who joined the room *after* this browser's own seedOpponents()
  // snapshot was taken (see lobby.js's onJoin — it seeds once, from whatever
  // /api/status said right before connecting) would otherwise stay
  // invisible in the Opponents panel until they happened to win a card or
  // take a Faux Pas — the only two spots that used to call ensureOpponent().
  // Every other event below now does the same create-if-missing, so a real
  // opponent shows up the moment they take their very first action (usually
  // within round 1), not whenever the first auction happens to resolve.
  // game.myUsername is null for spectators, so "!== game.myUsername" is
  // always true for them — every player they see is tracked as one of
  // game.opponents, which is exactly right since a spectator has no "my side".
  if (d.kind === 'auction_start') {
    game.maxBid = 0;
    game.myAuctionBid = 0;
    game.turnPlayer = d.starting_player;
    game.turnStartedAt = Date.now();
    if (d.starting_player !== game.myUsername) {
      ensureOpponent(d.starting_player);
      // Defensive backstop, independent of the move-timer's own interval
      // ever noticing expiration -- a turn_start/auction_start naming
      // someone else is unambiguous proof my own turn (if I had one) is
      // over. Without this, a backgrounded tab (browsers throttle
      // setInterval there, sometimes to once a minute+) could leave a
      // stale, already-wrong countdown visibly ticking long after the
      // server actually auto-passed and moved on -- a real reported bug
      // ("the clock is ticking even when I'm not playing").
      clearMoveTimer();
    }
    // Everyone's back in for the new auction — clear last round's greyed-out
    // state and their last-shown bid badge (see the 'bid' branch below).
    Object.values(game.opponents).forEach((o) => { o.outOfAuction = false; o.lastBid = null; });
    enqueueEvent(isSpectator, `New auction: ${describeCard(d.card)}`, 'start');
    logLine(`🃏 Auction #${d.round_number}: ${describeCard(d.card)}`, isSpectator);
  } else if (d.kind === 'turn_start') {
    game.turnPlayer = d.player;
    game.turnStartedAt = Date.now();
    if (d.player !== game.myUsername) {
      ensureOpponent(d.player);
      clearMoveTimer(); // see auction_start's identical guard above for why
    }
  } else if (d.kind === 'bid') {
    if (d.player === game.myUsername) {
      game.myAuctionBid = d.max_bid; // this event's max_bid is the bidder's own new cumulative total
      updateBidStatus();
    } else {
      // Same reasoning as myAuctionBid above: a 'bid' event's max_bid is
      // always the *acting* player's own new total (bidding, by
      // definition, raises the price past whatever it was), so this is
      // safe to store as that specific opponent's own running bid rather
      // than the auction-wide max — see gameRenderer.js's opponent-tile
      // badge, which shows it back per-player.
      ensureOpponent(d.player).lastBid = d.max_bid;
    }
    enqueueEvent(isSpectator, `${actorLabel(d.player)} raised to ${d.max_bid}`, 'bid');
    logLine(`💰 ${d.player} raised to ${d.max_bid}`, isSpectator);
  } else if (d.kind === 'pass' || d.kind === 'fold') {
    if (d.player !== game.myUsername) {
      const o = ensureOpponent(d.player);
      o.outOfAuction = true;
      o.lastBid = null; // no longer contesting -- nothing left to show a badge for
    }
    enqueueEvent(isSpectator, `${actorLabel(d.player)} passed`, 'pass');
    logLine(`⚪ ${d.player} passed`, isSpectator);
  } else if (d.kind === 'quit') {
    if (d.player !== game.myUsername) {
      const o = ensureOpponent(d.player);
      o.active = false;
      o.outOfAuction = true;
      o.lastBid = null;
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
    // Shows the panel the first time this ever fires (game just started,
    // before this player's first turn), in its usual "not your turn"
    // greyed state -- and stays correct on every later call too, since
    // renderMovePanel derives pending/interactive purely from
    // game.myPrompt rather than only fixing this up once.
    renderMovePanel();
  }
}

function applyPlayerMove(msg) {
  // A fresh prompt for us is just as much proof the server has moved
  // forward as an AUCTION_UPDATE is -- see that function's own comment.
  disarmActionWatchdog();
  if (!openMyPrompt(msg.data && msg.data.move_seq)) return;

  const bidControls = $('bid-controls');
  const discardControls = $('discard-controls');
  if (msg.move_type === 'discard_painting') {
    // Discard has no bid-error concept at all, so it's safe (and correct) to
    // clear out any leftover bid-rejection error here rather than leaving it
    // visible under the now-irrelevant discard controls.
    hide($('move-error'));
    bidControls.classList.add('hidden');
    discardControls.classList.remove('hidden');
    renderPaintingChoices(msg.constraints.allowed_paintings, setSelectedDiscardValue);
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
    // does something new (onPlaceBid/onPass/onResign each clear it before
    // sending), exactly matching the client-side "select at least one money
    // card" case.
    discardControls.classList.add('hidden');
    bidControls.classList.remove('hidden');
    game.selectedBid = new Set();
    renderMoneyChips(msg.constraints.allowed_money_cards);
    updateBidStatus();
  }
}

export function showReactionBubble(username, emoji, isSpectator) {
  let anchor;
  if (!isSpectator && game && username === game.myUsername) {
    anchor = $('my-panel');
  } else {
    const listId = isSpectator ? 'spec-players-list' : 'opponents-list';
    anchor = document.querySelector(`#${listId} .opponent-row[data-username="${CSS.escape(username)}"]`);
    // The .opponent-row DOM element only exists once renderOpponents() has
    // actually run since this player was last (re)known about -- ensureOpponent
    // only updates the in-memory game.opponents entry, not the DOM. A reaction
    // can easily arrive in the gap before that row has ever been rendered
    // (e.g. early in a round, before this opponent has bid/passed yet), which
    // used to make the bubble silently no-op with no visible symptom at all.
    // Force a render here and retry once before giving up.
    if (!anchor && game) {
      ensureOpponent(username);
      renderOpponents(isSpectator);
      anchor = document.querySelector(`#${listId} .opponent-row[data-username="${CSS.escape(username)}"]`);
    }
  }
  if (!anchor) return;
  const bubble = document.createElement('div');
  bubble.className = 'reaction-bubble';
  bubble.textContent = emoji;
  anchor.appendChild(bubble);
  requestAnimationFrame(() => bubble.classList.add('show'));
  setTimeout(() => bubble.remove(), 1600);
}
