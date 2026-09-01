// A scripted, step-through illustration of one auction of each type
// (normal vs. disgrace) on the dedicated How to Play screen -- see
// _how_to_play_rich.html. Entirely canned/fixed data: no backend call,
// no real game state, just a fixed sequence of {seats, card, narration}
// snapshots stepped through with Next/Prev. Simplified money (round
// numbers, one running total per seat) rather than the real game's
// discrete cash-denomination system -- the point here is teaching the
// bid/pass/win *mechanic*, not replicating the exact cash mechanics the
// rest of this page already explains in words.
import { $ } from '../utils/dom.js';

const SEAT_NAMES = ['You', 'Marble bot', 'Ziggy bot'];
const START_MONEY = 100;

// Each step: `card` ({label, kind}) and `seats` (money/bid/passed/active/
// winner per seat, indices matching SEAT_NAMES) describe the state to
// render *at* that step; `narration` is what's happening as it's reached.
const DEMOS = {
  normal: {
    cardLabel: '7',
    cardKind: 'neutral', // matches .status-card's own modifier classes
    steps: [
      {
        narration: 'A Painting worth 7 points is up for auction.',
        seats: [
          { money: START_MONEY }, { money: START_MONEY }, { money: START_MONEY },
        ],
      },
      {
        narration: 'You open the bidding at 3.',
        seats: [
          { money: START_MONEY, bid: 3, active: true }, { money: START_MONEY }, { money: START_MONEY },
        ],
      },
      {
        narration: 'Marble bot raises to 5.',
        seats: [
          { money: START_MONEY, bid: 3 }, { money: START_MONEY, bid: 5, active: true }, { money: START_MONEY },
        ],
      },
      {
        narration: "You pass -- you're out, and nothing was ever actually taken from you.",
        seats: [
          { money: START_MONEY, passed: true }, { money: START_MONEY, bid: 5 }, { money: START_MONEY },
        ],
      },
      {
        narration: 'Ziggy bot raises to 8. Nobody else raises again.',
        seats: [
          { money: START_MONEY, passed: true }, { money: START_MONEY, bid: 5 }, { money: START_MONEY, bid: 8, active: true },
        ],
      },
      {
        narration: 'Ziggy bot wins the Painting for 8 -- pays 8, everyone else keeps every chip they never actually spent.',
        seats: [
          { money: START_MONEY, passed: true }, { money: START_MONEY }, { money: START_MONEY - 8, winner: true },
        ],
      },
    ],
  },
  disgrace: {
    cardLabel: '🚫',
    cardKind: '', // the plain dark default .status-card face, same as Faux Pas' own gallery tile above
    steps: [
      {
        narration: "Faux Pas is up -- a disgrace auction. Remember: the FIRST to pass gets stuck with it.",
        seats: [
          { money: START_MONEY }, { money: START_MONEY }, { money: START_MONEY },
        ],
      },
      {
        narration: 'You raise to 2, hoping someone else passes first.',
        seats: [
          { money: START_MONEY, bid: 2, active: true }, { money: START_MONEY }, { money: START_MONEY },
        ],
      },
      {
        narration: 'Marble bot raises to 4 -- same idea, trying to outlast you.',
        seats: [
          { money: START_MONEY, bid: 2 }, { money: START_MONEY, bid: 4, active: true }, { money: START_MONEY },
        ],
      },
      {
        narration: 'Ziggy bot passes first -- stuck with Faux Pas, but keeps every chip.',
        seats: [
          { money: START_MONEY, bid: 2 }, { money: START_MONEY, bid: 4 }, { money: START_MONEY, passed: true, winner: true },
        ],
      },
      {
        narration: 'You and Marble bot get your raised money back -- you never actually spent it. Ziggy bot must now discard a Painting.',
        seats: [
          { money: START_MONEY }, { money: START_MONEY }, { money: START_MONEY, winner: true },
        ],
      },
    ],
  },
};

let currentDemo = 'normal';
let currentStep = 0;

function renderStep() {
  const demo = DEMOS[currentDemo];
  const step = demo.steps[currentStep];

  const card = $('rules-demo-card');
  card.textContent = demo.cardLabel;
  card.className = `status-card big ${demo.cardKind}`.trim();

  $('rules-demo-seats').innerHTML = step.seats.map((s, i) => {
    const classes = ['rules-demo-seat', s.active && 'active', s.winner && 'winner'].filter(Boolean).join(' ');
    const badge = s.passed
      ? '<span class="rules-demo-seat-passed">Passed</span>'
      : (s.bid != null ? `<span class="rules-demo-seat-bid">Bid ${s.bid}</span>` : '');
    return `
      <div class="${classes}">
        <div class="rules-demo-seat-name">${SEAT_NAMES[i]}</div>
        <div class="rules-demo-seat-money">${s.money}</div>
        ${badge}
      </div>
    `;
  }).join('');

  $('rules-demo-narration').textContent = step.narration;
  $('rules-demo-step-label').textContent = `${currentStep + 1} / ${demo.steps.length}`;
  $('btn-rules-demo-prev').disabled = currentStep === 0;
  const isLastStep = currentStep === demo.steps.length - 1;
  $('btn-rules-demo-next').textContent = isLastStep ? 'Restart ↺' : 'Next →';
}

export function showRulesDemo(type) {
  currentDemo = type;
  currentStep = 0;
  renderStep();
}

export function onRulesDemoToggleClick(e) {
  const btn = e.target.closest('[data-demo]');
  if (!btn) return;
  document.querySelectorAll('#rules-demo-toggle [data-demo]').forEach((b) => b.classList.toggle('selected', b === btn));
  showRulesDemo(btn.dataset.demo);
}

export function onRulesDemoPrevClick() {
  if (currentStep > 0) { currentStep -= 1; renderStep(); }
}

export function onRulesDemoNextClick() {
  const demo = DEMOS[currentDemo];
  if (currentStep < demo.steps.length - 1) { currentStep += 1; } else { currentStep = 0; } // "Restart ↺" on the last step, per renderStep's own label swap
  renderStep();
}
