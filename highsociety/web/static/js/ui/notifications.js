// App-wide and mid-auction notifications: the generic toast, the transient
// event-toast queue, the game-start/final-green overlays, and the game log.
import { $, hide, show } from '../utils/dom.js';
import { TOAST_DURATION_MS, RESULT_TOAST_DURATION_MS } from '../utils/constants.js';
import { ordinal } from '../utils/formatting.js';

let appToastTimer = null;
// Generic one-off notification reachable from any screen (e.g. the guest
// sign-in prompt on the Account screen's Achievements section) -- distinct
// from the mid-auction .event-toast, which is pinned to the game card and
// driven by the event queue. .hidden uses display:none (can't transition),
// so this removes it first and lets a fresh frame land before adding .show,
// same two-step dance as the event toast's fade-in.
export function showToast(message, durationMs = 3500) {
  const toast = $('app-toast');
  if (appToastTimer) { clearTimeout(appToastTimer); appToastTimer = null; }
  toast.textContent = message;
  show(toast);
  requestAnimationFrame(() => toast.classList.add('show'));
  appToastTimer = setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => hide(toast), 250);
  }, durationMs);
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

export function enqueueEvent(isSpectator, text, tone) {
  const key = isSpectator ? 'spec' : 'game';
  const duration = (tone === 'buy' || tone === 'disgrace') ? RESULT_TOAST_DURATION_MS : TOAST_DURATION_MS;
  eventQueue[key].push({ text, tone, duration });
  pumpEventQueue(key);
}

function pumpEventQueue(key) {
  if (eventQueueBusy[key] || eventQueue[key].length === 0) return;
  eventQueueBusy[key] = true;
  const { text, tone, duration } = eventQueue[key].shift();
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
  }, duration);
}

// The game-ending green card gets a dedicated, unmissable overlay rather
// than going through enqueueEvent/the toast queue — it's a one-off moment
// that shouldn't be interruptible or cut short by whatever narration would
// normally queue up next (and nothing does queue up after it anyway, since
// the game ends here). Self-cleans visually the instant showScreen() hides
// this screen for the finished screen, so no extra coordination is needed
// between this timer and the screen transition.
export function showFinalGreenOverlay(isSpectator, count) {
  const overlay = $(isSpectator ? 'spec-final-green-overlay' : 'final-green-overlay');
  overlay.querySelector('.final-green-title').textContent = `${ordinal(count)} Green Card Revealed!`;
  overlay.classList.remove('show');
  void overlay.offsetWidth; // restart the entrance animation even on a rapid repeat
  overlay.classList.add('show');
  clearTimeout(overlay._hideTimer);
  overlay._hideTimer = setTimeout(() => overlay.classList.remove('show'), 4000);
}

// The pre-game overlay is a quiet "Starting soon…" with a CSS-only animated
// ellipsis (see .game-start-dots in style.css) — no per-tick number to
// render, so unlike the old 3-2-1 version there's nothing that can flash by
// too fast to read. The server still sends one 'countdown' event per second
// (gameplay.py's countdown_to_start) plus a final 'countdown_finished', but
// this only cares about the first and last: show once, hide once. The one
// remaining timing risk — a slow/cold connection buffering the *entire*
// countdown into a single burst, show immediately followed by hide — is
// guarded by a minimum-visible floor so the overlay can't flash for less
// than that even in the worst case.
const countdownShownAt = { game: null, spec: null };
const COUNTDOWN_MIN_VISIBLE_MS = 900;

export function showCountdownOverlay(isSpectator) {
  const key = isSpectator ? 'spec' : 'game';
  const overlay = $(key === 'spec' ? 'spec-game-start-overlay' : 'game-start-overlay');
  if (overlay.classList.contains('show')) return;
  countdownShownAt[key] = Date.now();
  overlay.classList.add('show');
}

// The countdown's final tick — clear the overlay so the first real auction
// underneath takes over immediately, no separate "Game Started!" message.
export function hideCountdownOverlay(isSpectator) {
  const key = isSpectator ? 'spec' : 'game';
  const overlay = $(key === 'spec' ? 'spec-game-start-overlay' : 'game-start-overlay');
  const shownAt = countdownShownAt[key];
  const elapsed = shownAt ? Date.now() - shownAt : COUNTDOWN_MIN_VISIBLE_MS;
  const wait = Math.max(0, COUNTDOWN_MIN_VISIBLE_MS - elapsed);
  setTimeout(() => {
    overlay.classList.remove('show');
    countdownShownAt[key] = null;
  }, wait);
}

// ---------------------------------------------- tutorial coaching card --
//
// One-time teaching callouts for the guided first-game tutorial (see
// gameEvents.js's maybeShowTutorialCoach) -- a separate queue from the
// transient event toast above, since a coaching card needs to stay up long
// enough to actually read (TUTORIAL_COACH_DURATION_MS) and can be dismissed
// early by the player, neither of which the event toast's fixed, brief,
// dismiss-proof timing supports.
const TUTORIAL_COACH_DURATION_MS = 9000;
const tutorialCoachQueue = [];
let tutorialCoachBusy = false;
let tutorialCoachHideTimer = null;

// Same icon + color coding as the three cue cards on the How to Play screen
// (_how_to_play_rich.html) -- 'normal' is the plain amber warning triangle
// (the "don't spend everything" card), 'disgrace' is the green-tinted
// .rules-cue-card-flip swap-arrows icon (the trade-off card), 'green' is the
// plain circle with .rules-cue-card-icon-green (the "4th green card" card).
// A tutorial player who's just read one of these on How to Play sees the
// exact same icon/color mean the exact same thing in the live game.
const TUTORIAL_COACH_KINDS = {
  normal: {
    cardClass: '',
    iconClass: '',
    iconSvg: '<path d="M12 9v4"/><path d="M12 16.5h.01"/>'
      + '<path d="M10.3 3.9 2.6 17.5a1.8 1.8 0 0 0 1.56 2.7h15.68a1.8 1.8 0 0 0 1.56-2.7L13.7 3.9a1.8 1.8 0 0 0-3.4 0Z"/>',
  },
  disgrace: {
    cardClass: 'rules-cue-card-flip',
    iconClass: '',
    iconSvg: '<path d="M17 2.1l4 4-4 4"/><path d="M3 12.1v-2a4 4 0 0 1 4-4h14"/>'
      + '<path d="M7 21.9l-4-4 4-4"/><path d="M21 11.9v2a4 4 0 0 1-4 4H3"/>',
  },
  green: {
    cardClass: '',
    iconClass: 'rules-cue-card-icon-green',
    iconSvg: '<circle cx="12" cy="12" r="8.5"/>',
  },
};

export function enqueueTutorialCoach(text, kind) {
  tutorialCoachQueue.push({ text, kind });
  pumpTutorialCoachQueue();
}

function pumpTutorialCoachQueue() {
  if (tutorialCoachBusy || tutorialCoachQueue.length === 0) return;
  tutorialCoachBusy = true;
  const { text, kind } = tutorialCoachQueue.shift();
  const spec = TUTORIAL_COACH_KINDS[kind] || TUTORIAL_COACH_KINDS.normal;
  const card = $('tutorial-coach-card');
  card.classList.remove('rules-cue-card-flip');
  if (spec.cardClass) card.classList.add(spec.cardClass);
  const icon = $('tutorial-coach-card-icon');
  icon.className = `rules-cue-card-icon${spec.iconClass ? ` ${spec.iconClass}` : ''}`;
  icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${spec.iconSvg}</svg>`;
  $('tutorial-coach-card-text').textContent = text;
  show(card);
  requestAnimationFrame(() => card.classList.add('show'));
  tutorialCoachHideTimer = setTimeout(hideTutorialCoach, TUTORIAL_COACH_DURATION_MS);
}

// Shared by the auto-timeout above and the card's own dismiss button
// (app.js) -- either way the card fades out, the slot frees up, and
// whatever's next in the queue (if anything) takes its place.
export function hideTutorialCoach() {
  clearTimeout(tutorialCoachHideTimer);
  const card = $('tutorial-coach-card');
  card.classList.remove('show');
  setTimeout(() => {
    hide(card);
    tutorialCoachBusy = false;
    pumpTutorialCoachQueue();
  }, 250); // let the fade-out clear before the next card (if any) claims the slot
}

export function logLine(text, isSpectator) {
  if (!text) return;
  const el = $(isSpectator ? 'spec-game-log' : 'game-log');
  const p = document.createElement('div');
  p.textContent = text;
  el.appendChild(p);
  el.scrollTop = el.scrollHeight;
}
