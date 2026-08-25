// All DOM rendering for the live game screen: the auction panel, the move
// panel, opponents, card display, and the per-move countdown timer.
import { $ } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { CLOCK_ICON_SVG, CARD_INFO_TEXT } from '../utils/constants.js';
import { game, ensureOpponent, computePoints, answerMyPrompt } from './gameState.js';

// ---------------------------------------------------------------- cards --

// "You" instead of your own username in event toasts — reads more naturally
// when it's your own action being announced back to you. Spectators have no
// game.myUsername (it's null), so this is a no-op for them; they always see
// real names, which is correct since they aren't a player at the table.
export function actorLabel(username) {
  return game && username === game.myUsername ? 'You' : username;
}

export function describeCard(card) {
  const names = { Painting: `Painting (${card.value})`, PrestigeCard: 'Prestige Card (×2)',
    FauxPas: 'Faux Pas', Passe: 'Passe (−5)', Scandale: 'Scandale (½×, green)' };
  return names[card.type] || card.type;
}

// Just the type name, no value/effect -- describeCard()'s "(9)"/"(×2)" is
// redundant here since the card face right below already shows that part.
export function cardTypeName(card) {
  const names = { Painting: 'Painting', PrestigeCard: 'Prestige Card',
    FauxPas: 'Faux Pas', Passe: 'Passe', Scandale: 'Scandale' };
  return names[card.type] || card.type;
}

// Color coding is deliberately just green-vs-not: Prestige and Scandale are
// the two actual "green cards" (see is_green / the green_card_limit rule),
// so only they get real green — every other card shares one neutral tone
// rather than each type having its own color, keeping green a meaningful
// signal instead of one hue among several.
export function cardLabel(card) {
  switch (card.type) {
    case 'Painting': return { cls: 'neutral', text: String(card.value) };
    case 'PrestigeCard': return { cls: 'green', text: '×2' };
    case 'FauxPas': return { cls: 'neutral', text: 'Faux Pas' };
    case 'Passe': return { cls: 'neutral', text: '−5' };
    case 'Scandale': return { cls: 'green', text: '½×' };
    default: return { cls: '', text: card.type };
  }
}

export function cardEl(card, big) {
  const { cls, text } = cardLabel(card);
  const div = document.createElement('div');
  div.className = `status-card ${cls}${big ? ' big' : ''}`;
  div.innerHTML = `<span class="value">${text}</span>${card.is_green ? '<span class="green-dot"></span>' : ''}`;
  if (card.description) div.title = card.description;
  return div;
}

export function cardBackEl() {
  const div = document.createElement('div');
  div.className = 'card-back';
  div.title = 'Hidden. Enable "Reveal cards" to see what this is.';
  return div;
}

// ------------------------------------------------------------ move timer --

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

export function startMoveTimer(secondsRemaining) {
  clearMoveTimer();
  moveTimerDeadline = Date.now() + secondsRemaining * 1000;
  updateMoveTimerDisplay();
  moveTimerInterval = setInterval(updateMoveTimerDisplay, 250);
}

export function clearMoveTimer() {
  if (moveTimerInterval) { clearInterval(moveTimerInterval); moveTimerInterval = null; }
  moveTimerDeadline = null;
  moveTimerUrgentAnnounced = false;
  $('move-timer').classList.add('hidden');
}

// How many seconds before zero the clock should turn urgent — scaled to the
// room's actual per-move limit rather than a flat 5s, since 5s left out of
// a 20s move reads very differently than 5s left out of a 3-minute one.
export function urgentWindowSeconds() {
  const limit = game && game.turnTimeLimit;
  if (!limit || limit < 30) return 5;
  if (limit <= 180) return 15; // >30s and up through 3 minutes
  return 30; // beyond 3 minutes
}

function updateMoveTimerDisplay() {
  const remaining = Math.max(0, (moveTimerDeadline - Date.now()) / 1000);
  const el = $('move-timer');
  const secondsLeft = Math.ceil(remaining);
  el.innerHTML = `${CLOCK_ICON_SVG}${secondsLeft}s left`;
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
    answerMyPrompt();
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

// ------------------------------------------------------------- rendering --

// Single place that derives #move-panel's visibility/interactivity from
// game.myPrompt -- the one source of truth for "do I currently have
// something to answer, and have I already." Called from every place that
// changes myPrompt (gameState.js's openMyPrompt/answerMyPrompt) plus
// gameEvents.js's INPUT_ERROR handling (which reopens myPrompt for another
// attempt without a fresh PLAYER_MOVE having arrived yet). Safe to call
// repeatedly/idempotently -- it always derives the full correct state from
// scratch rather than toggling incrementally.
export function renderMovePanel() {
  const panel = $('move-panel');
  panel.classList.remove('hidden');
  panel.classList.toggle('pending', !game.myPrompt || game.myPrompt.answered);
  renderAuctionPanel(false); // the turn label depends on game.myPrompt too
}

export function renderAuctionPanel(isSpectator) {
  const prefix = isSpectator ? 'spec-' : '';
  $(`${prefix}round-label`).innerHTML = game.round ? `<span class="suit-icon">🂠</span> Auction <strong>#${game.round}</strong>` : '';
  // No separate "whose turn" treatment here beyond this label — it already
  // has its own pulsing dot, and the auction panel otherwise represents
  // shared state (card, bid) that stays fully legible regardless of whose
  // turn it is, not something that dims/greys based on turn.
  // An open, unanswered game.myPrompt is the authoritative "it's my turn"
  // signal (set only by openMyPrompt, right alongside game.turnPlayer --
  // see its own comment), with game.turnPlayer === game.myUsername kept as
  // a harmless read-time fallback for the brief window right after a
  // reconnect's "sync" AUCTION_UPDATE reports the turn before the real
  // PLAYER_MOVE re-prompt has arrived to open myPrompt yet. This is safe
  // precisely because it's a read-time OR of two already-correct values,
  // not a third mutable flag some handler could forget to write -- that
  // was the actual bug (multiple call sites each independently responsible
  // for keeping turnPlayer in sync), not the comparison itself.
  const iAmUp = (game.myPrompt && !game.myPrompt.answered) || game.turnPlayer === game.myUsername;
  const turnText = iAmUp ? 'Your turn' : `${escapeHtml(game.turnPlayer)}'s turn`;
  $(`${prefix}turn-label`).innerHTML = (iAmUp || game.turnPlayer)
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

  $(`${prefix}auction-card-type`).textContent = game.card ? cardTypeName(game.card) : '';

  const cardContainer = $(`${prefix}auction-card`);
  cardContainer.innerHTML = '';
  if (game.card) cardContainer.appendChild(cardEl(game.card, true));
  renderOpponents(isSpectator);
  updateCardInfoButton(isSpectator);
}

// Keeps the ⓘ button (and its popover's contents) next to the auction card
// in sync with whatever's currently up for auction. Hidden entirely when
// there's no card up (e.g. between auctions) — the popover itself also gets
// force-closed at that point so it can't be left open showing stale text
// into the next auction. Reuses describeCard() so the popover's title always
// matches the label already shown elsewhere for this same card.
export function updateCardInfoButton(isSpectator) {
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
// handler in gameEvents.js for where game.playerOrder comes from). Rotated
// to start right after "me" for players, so the list always reads
// top-to-bottom in the exact order turns will actually advance, wrapping
// bottom-to-top -- even when the shuffle put you mid-cycle rather than
// first. Spectators have no seat of their own to rotate around, so they
// just see the raw seat order start to finish.
export function orderedOpponentUsernames(isSpectator) {
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

export function renderOpponents(isSpectator) {
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
      // Two explicit sub-groups (not 3 flat children space-between'd)
      // so the bid badge sits directly next to the name it belongs to,
      // rather than floating in its own slot between the timer and points.
      row.innerHTML = '<div class="opponent-header">'
        + '<div class="opponent-header-left"><span class="name"></span><span class="bid-badge"></span></div>'
        + '<div class="opponent-header-right"><span class="opp-timer"></span><span class="pts"></span></div>'
        + '</div>'
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

    // Live countdown for whichever opponent's turn it currently is —
    // same math as your own move-timer (startMoveTimer), just anchored to
    // game.turnStartedAt (set when their turn_start/auction_start arrived)
    // instead of a value this specific client was sent. Only meaningful
    // for a timed room; untimed rooms never set turnTimeLimit, so this
    // stays blank for everyone, same as today.
    const timerEl = row.querySelector('.opp-timer');
    if (isCurrentTurn && game.turnTimeLimit && game.turnStartedAt) {
      const remaining = Math.max(0, game.turnTimeLimit - (Date.now() - game.turnStartedAt) / 1000);
      timerEl.innerHTML = `${CLOCK_ICON_SVG}${Math.ceil(remaining)}s`;
      timerEl.classList.toggle('urgent', remaining > 0 && remaining <= urgentWindowSeconds());
    } else {
      timerEl.textContent = '';
      timerEl.classList.remove('urgent');
    }

    // Shows this opponent's own current committed bid for the live
    // auction, cleared the moment they pass/fold/quit or a new auction
    // starts (see gameEvents.js's applyAuctionUpdate) -- blank rather than
    // a badge showing "0" whenever they haven't bid at all this round.
    const bidBadge = row.querySelector('.bid-badge');
    bidBadge.textContent = o.lastBid ? `Bid: ${o.lastBid}` : '';

    const chips = row.querySelector('.chip-row');
    chips.innerHTML = '';
    o.statusCards.forEach((c) => chips.appendChild(game.revealCards ? cardEl(c) : cardBackEl()));
  });

  container.querySelectorAll('.opponent-row').forEach((row) => {
    if (!seenUsernames.has(row.dataset.username)) row.remove();
  });
}

// Ticks the opponents list often enough for the live per-opponent
// countdown above to actually look live, rather than only updating
// whenever some unrelated event happens to trigger a re-render. Runs
// unconditionally for the life of the page — renderOpponents() itself
// already no-ops immediately if there's no game in progress, and once
// there is one, this is the same cheap "update existing rows in place"
// path every other event already drives, just on a timer instead of an
// event. Coarser than your own move-timer's 250ms tick (that one gates a
// real auto-pass-adjacent deadline you're expected to act on; this is
// just a glance at someone else's).
setInterval(() => { renderOpponents(false); renderOpponents(true); }, 500);

export function renderMyPanel() {
  $('my-username-label').textContent = game.myUsername || '';
  $('my-points').textContent = game.myPoints;
  const chips = $('my-status-cards');
  chips.innerHTML = '';
  game.myStatusCards.forEach((c) => chips.appendChild(cardEl(c)));
}

export function renderMoneyChips(values) {
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
export function updateBidStatus() {
  $('my-current-bid').textContent = game.myAuctionBid;
  $('bid-need-more').textContent = game.maxBid > 0 ? `(add more than ${game.maxBid - game.myAuctionBid} to raise)` : '';
  updateSelectedBidTotal();
}

export function updateSelectedBidTotal() {
  const addingTotal = [...game.selectedBid].reduce((a, b) => a + b, 0);
  $('selected-bid').textContent = addingTotal;
  $('new-total-bid').textContent = game.myAuctionBid + addingTotal;
}

// The .selected CSS class on a money chip is only ever *added* by the
// click handler above -- clearing game.selectedBid (the actual set of
// values) doesn't touch it. onPlaceBid clears the set right after sending
// a bid (see its own comment for why), but without this, the chips stayed
// visually highlighted regardless of whether the bid was then accepted or
// rejected -- a rejected bid reopens the same prompt with "Adding: 0" (the
// set is genuinely empty) while the just-submitted chips still looked
// selected, a real, live-reported mismatch between the visual and the
// actual state.
export function clearSelectedBidVisual() {
  $('my-money-cards').querySelectorAll('.chip.selected').forEach((el) => el.classList.remove('selected'));
}

export function renderPaintingChoices(values, onSelect) {
  const row = $('my-paintings');
  row.innerHTML = '';
  $('btn-discard-painting').disabled = true;
  values.forEach((value) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip neutral';
    btn.textContent = value;
    btn.addEventListener('click', () => {
      row.querySelectorAll('button.chip').forEach((b) => b.classList.remove('selected'));
      btn.classList.add('selected');
      $('btn-discard-painting').disabled = false;
      onSelect(value);
    });
    row.appendChild(btn);
  });
}
