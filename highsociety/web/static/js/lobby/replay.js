// Replay screen: steps through a finished game with a slider, arrows and
// autoplay. The server sends the whole table at every moment (see
// recording/game_replay.py's replay_steps), so moving anywhere is just drawing
// that step: there's nothing to rewind.
import { $, hide, show, showScreen, setScreenPath } from '../utils/dom.js';
import { cardEl, cardTypeName } from '../game/gameRenderer.js';
import { fetchJSON } from './lobby.js';

const AUTOPLAY_MS = 1200;

let replay = null;
let stepIndex = 0;
let autoplayTimer = null;
let loadingGameId = null; // stale-response guard: the newest screen open wins
let returnTo = null;

export function getReplayReturnTo() {
  return returnTo;
}

function nameOf(seat) {
  return replay.seats[seat].name;
}

// Why a move happened without the player choosing it, if it did.
const FORCED = { timeout: ' (ran out of time)', disconnected: ' (disconnected)' };

function describeStep(step) {
  const who = step.seat != null ? nameOf(step.seat) : '';
  const forced = FORCED[step.reason] || '';
  switch (step.kind) {
    case 'auction_start': return `New auction: ${cardTypeName(step.card)}`;
    case 'bid': return `${who} bid ${step.amount}`;
    case 'pass':
    case 'fold': return `${who} passed${forced}`;
    case 'quit': return `${who} left the auction${forced}`;
    case 'discard': return `${who} discarded a painting worth ${step.amount}`;
    case 'disconnect': return `${who} lost connection`;
    case 'reconnect': return `${who} reconnected`;
    case 'resign': return `${who} resigned`;
    case 'auction_result': {
      if (step.seat == null) return 'Nobody took this card';
      if (['FauxPas', 'Passe', 'Scandale'].includes(step.card.type)) return `${who} is stuck with ${cardTypeName(step.card)}`;
      return `${who} bought ${cardTypeName(step.card)} for ${step.amount}`;
    }
    case 'game_over': {
      const winners = replay.standings.filter((s) => s.is_winner).map((s) => nameOf(s.seat));
      if (!winners.length) return 'Game over: no winner';
      return `Game over: ${winners.join(' and ')} won`;
    }
    default: return '';
  }
}

function seatRow(player, seat, step) {
  const row = document.createElement('div');
  row.className = 'replay-seat';
  row.classList.toggle('acting', step.seat === seat && step.kind !== 'game_over');
  row.classList.toggle('out', !player.active);

  const head = document.createElement('div');
  head.className = 'replay-seat-head';
  const name = document.createElement('strong');
  name.className = 'replay-seat-name';
  name.textContent = nameOf(seat); // text, never markup: names come from players
  head.appendChild(name);

  const standing = step.kind === 'game_over' ? replay.standings.find((s) => s.seat === seat) : null;
  const tag = (() => {
    if (standing && standing.left) return 'Left the game';
    if (standing && standing.is_winner) return 'Winner';
    if (standing && standing.eliminated) return 'Least money';
    if (!player.active) return 'Away';
    if (step.kind !== 'game_over' && !player.in_auction) return 'Passed';
    return '';
  })();
  if (tag) {
    const tagEl = document.createElement('span');
    tagEl.className = `replay-seat-tag${tag === 'Winner' ? ' winner' : ''}`;
    tagEl.textContent = tag;
    head.appendChild(tagEl);
  }
  const points = document.createElement('span');
  points.className = 'replay-seat-points';
  points.textContent = `${player.points} ${player.points === 1 ? 'pt' : 'pts'}`;
  head.appendChild(points);
  row.appendChild(head);

  const cards = document.createElement('div');
  cards.className = 'replay-seat-cards';
  player.cards.forEach((card) => cards.appendChild(cardEl(card, false)));
  row.appendChild(cards);

  const money = document.createElement('div');
  money.className = 'replay-seat-money';
  const addChips = (values, cls) => values.forEach((value) => {
    const chip = document.createElement('span');
    chip.className = cls;
    chip.textContent = value;
    money.appendChild(chip);
  });
  addChips(player.money, 'replay-chip');
  addChips(player.bid, 'replay-chip bid'); // committed cards have already left the hand
  row.appendChild(money);
  return row;
}

function render() {
  const step = replay.steps[stepIndex];
  $('replay-round').textContent = step.round ? `Auction ${step.round}` : '';
  $('replay-event').textContent = describeStep(step);
  $('replay-event').dataset.kind = step.kind;
  // Nothing is up for auction once the game's over; the space stays reserved.
  const card = step.kind === 'game_over' ? null : step.card;
  $('replay-card-type').textContent = card ? cardTypeName(card) : '';
  const slot = $('replay-card');
  slot.innerHTML = '';
  if (card) slot.appendChild(cardEl(card, true));
  $('replay-top-bid').textContent = step.kind === 'game_over' ? '–' : step.top_bid;

  const seats = $('replay-seats');
  seats.innerHTML = '';
  step.players.forEach((player, seat) => seats.appendChild(seatRow(player, seat, step)));

  const slider = $('replay-slider');
  slider.value = stepIndex;
  slider.setAttribute('aria-valuetext', describeStep(step));
  $('replay-position').textContent = `${stepIndex + 1} / ${replay.steps.length}`;
  const atStart = stepIndex === 0;
  const atEnd = stepIndex === replay.steps.length - 1;
  $('btn-replay-first').disabled = atStart;
  $('btn-replay-prev').disabled = atStart;
  $('btn-replay-next').disabled = atEnd;
  $('btn-replay-last').disabled = atEnd;
}

function goTo(index) {
  stepIndex = Math.max(0, Math.min(replay.steps.length - 1, index));
  render();
}

function stopAutoplay() {
  if (autoplayTimer) clearInterval(autoplayTimer);
  autoplayTimer = null;
  $('btn-replay-play').textContent = '▶';
  $('btn-replay-play').setAttribute('aria-label', 'Play');
}

function toggleAutoplay() {
  if (autoplayTimer) { stopAutoplay(); return; }
  if (stepIndex === replay.steps.length - 1) goTo(0);
  $('btn-replay-play').textContent = '❚❚';
  $('btn-replay-play').setAttribute('aria-label', 'Pause');
  autoplayTimer = setInterval(() => {
    if ($('screen-replay').classList.contains('hidden')) { stopAutoplay(); return; }
    goTo(stepIndex + 1);
    if (stepIndex === replay.steps.length - 1) stopAutoplay();
  }, AUTOPLAY_MS);
}

// The start of the auction before (or after) this moment. "Before" from the
// middle of an auction lands on that auction's own start first.
function auctionStartIndex(direction) {
  const steps = replay.steps;
  for (let i = stepIndex + direction; i >= 0 && i < steps.length; i += direction) {
    if (steps[i].kind === 'auction_start') return i;
  }
  return direction < 0 ? 0 : steps.length - 1;
}

export async function showReplayScreen(gameId, from = null) {
  stopAutoplayIfLoaded();
  replay = null;
  returnTo = from;
  loadingGameId = gameId;
  showScreen('screen-replay');
  setScreenPath(`/replay/${gameId}`);
  hide($('replay-unavailable'));
  hide($('replay-table'));
  show($('replay-loading'));
  let result = null;
  let failure = null;
  try {
    result = await fetchJSON(`/api/games/replay/${encodeURIComponent(gameId)}`);
  } catch (e) {
    failure = e;
  }
  if (loadingGameId !== gameId) return;
  hide($('replay-loading'));
  if (!result) {
    $('replay-unavailable').textContent = failure && failure.status === 409
      ? 'This game can\'t be replayed. It was played under older rules, or before replays were recorded.'
      : 'Couldn\'t load this replay. Try again in a moment.';
    show($('replay-unavailable'));
    return;
  }
  replay = result;
  stepIndex = 0;
  $('replay-date').textContent = new Date(result.finished_at).toLocaleDateString();
  $('replay-slider').max = result.steps.length - 1;
  show($('replay-table'));
  render();
}

function stopAutoplayIfLoaded() {
  if (autoplayTimer) stopAutoplay();
}

function moveBy(action) {
  if (!replay) return;
  stopAutoplay();
  action();
}

// Wired once at startup from app.js.
export function initReplay() {
  $('btn-replay-first').addEventListener('click', () => moveBy(() => goTo(auctionStartIndex(-1))));
  $('btn-replay-prev').addEventListener('click', () => moveBy(() => goTo(stepIndex - 1)));
  $('btn-replay-next').addEventListener('click', () => moveBy(() => goTo(stepIndex + 1)));
  $('btn-replay-last').addEventListener('click', () => moveBy(() => goTo(auctionStartIndex(1))));
  $('btn-replay-play').addEventListener('click', () => { if (replay) toggleAutoplay(); });
  $('replay-slider').addEventListener('input', (e) => moveBy(() => goTo(parseInt(e.target.value, 10))));
  document.addEventListener('keydown', (e) => {
    if (!replay || $('screen-replay').classList.contains('hidden')) return;
    if (e.target.matches('input:not([type="range"]), textarea')) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); moveBy(() => goTo(stepIndex - 1)); }
    if (e.key === 'ArrowRight') { e.preventDefault(); moveBy(() => goTo(stepIndex + 1)); }
  });
}
