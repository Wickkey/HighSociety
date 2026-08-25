// Owns `game` -- the one source of truth for live game state. A named
// `let` export is a live binding in ES modules: every other module that
// does `import { game } from './gameState.js'` and reads `game.foo` always
// sees the current object, because only this file ever reassigns the
// `game` variable itself (in resetGameState below). Everywhere else reads
// game.* directly or mutates fields on the existing object -- nothing
// outside this file replaces the whole object.
// Circular imports with gameRenderer.js/lobby.js/gameActions.js are expected
// and safe here: every cross-import below is only ever called from inside a
// function body (never at this module's own top-level evaluation), by which
// point all four modules have finished loading.
import { $ } from '../utils/dom.js';
import { clearMoveTimer, renderAuctionPanel, renderMyPanel, renderMoneyChips, renderMovePanel } from './gameRenderer.js';
import { setHasResigned } from '../lobby/lobby.js';
import { resetSelectedDiscard } from './gameActions.js';

export let game = null;

// Whether opponents' actual won cards/points are shown, or kept hidden
// behind card-backs, and whether the game-log panel is shown at all — both
// are host-time settings (see host-reveal-cards/host-show-logs in the
// lobby form), fixed for the whole table once the game starts rather than
// a per-player runtime toggle. `status` is whatever /api/status last said
// about this room; defaults to "on" if not known yet.
export function resetGameState(myUsername, status) {
  game = {
    round: 0,
    card: null,
    maxBid: 0,
    myAuctionBid: 0, // my own cumulative committed bid for the *current* auction only
    turnPlayer: null,
    // When the current turnPlayer's turn actually started, client-side --
    // lets renderOpponents show a live countdown for *whoever's* turn it
    // is, the same math startMoveTimer already uses for your own turn,
    // just anchored to this instead of a server-sent deadline. Approximate
    // (a few hundred ms of real network latency before this client even
    // saw the turn_start that set it), which is fine for a glance at an
    // opponent's clock -- the real deadline enforced server-side never
    // depends on this.
    turnStartedAt: null,
    myUsername,
    myPoints: 0,
    myStatusCards: [],
    selectedBid: new Set(),
    // The one source of truth for "do I currently have an open decision,
    // and have I already answered it" -- see openMyPrompt/answerMyPrompt/
    // renderMovePanel in gameRenderer.js. Living *on* game (not as sibling
    // module variables) is deliberate: this whole object gets replaced on
    // every new game/rematch/reconnect right here, so there is no separate
    // line to remember to reset when a new per-game concern gets added
    // later -- it happens for free, structurally, by virtue of living in
    // this object.
    myPrompt: null,
    // Dedupe/staleness memory for openMyPrompt, independent of myPrompt
    // itself (which gets replaced by every new prompt) -- see gameplay.py's
    // _move_sequence: each PlayGame (including a rematch's brand-new one)
    // starts its own counter near zero, so this must live here too rather
    // than survive across a resetGameState call.
    highestAnsweredMoveSeq: null,
    opponents: {}, // username -> {name, statusCards: [], active: true, outOfAuction: false, lastBid: null}
    // The real post-shuffle seat/turn order (see gameplay.py's player_order
    // broadcast) -- empty until that arrives, which renderOpponents falls
    // back to plain insertion order for.
    playerOrder: [],
    revealCards: status ? status.reveal_cards !== false : true,
    showLogs: status ? status.show_logs !== false : true,
    // The room's fixed per-move timer (seconds, or null/undefined for no
    // limit) — used only to scale the move-timer's "urgent" warning window
    // (see urgentWindowSeconds), not re-sent per move.
    turnTimeLimit: status ? status.turn_time_limit : null,
    // Shown in-game (see applyRoomDisplaySettings) so a game can be
    // reported/reproduced precisely -- "it happened in seed 12345" -- but
    // only when manualSeed is true: an auto-rolled seed nobody chose
    // isn't a useful number to show, just noise.
    seed: status ? status.seed : null,
    manualSeed: status ? !!status.manual_seed : false,
  };
  // Every other piece of state whose lifetime is "this one active game" --
  // not already inside the `game` object above -- must be reset here too.
  // This is the one place a new game's slate gets wiped clean; the next
  // person adding per-game state should either put it on `game` itself
  // (preferred -- see myPrompt's own comment above) or export a reset hook
  // from whichever module owns it and call it here, not leave it to a
  // sibling module variable nobody remembers to touch. clearMoveTimer()
  // closes a real gap: an untimed rematch played right after a timed game
  // would otherwise keep ticking down the old game's stale deadline, with
  // no fresh PLAYER_MOVE_TIMER ever arriving to override it. The
  // hasResigned/selectedDiscardValue resets (see lobby.js/gameActions.js)
  // close two more of the same shape.
  clearMoveTimer();
  setHasResigned(false);
  resetSelectedDiscard();
  // logLine()/appendChatLine() only ever append — without this, a rematch
  // (or any other resetGameState call, e.g. reconnecting into a genuinely
  // new game) left the previous game's whole narration log, and chat,
  // sitting above whatever the new game starts writing underneath it.
  ['game-log', 'spec-game-log', 'player-chat-log', 'spec-chat-log'].forEach((id) => { $(id).innerHTML = ''; });
  // resetGameState only just rebuilt the in-memory `game` object above --
  // the DOM itself still shows whatever the *previous* game (or, for a
  // spectator/reconnect, whatever this tab happened to render before)
  // last rendered: the old auction card, opponents, points. That stays
  // visible until the new game's first live event overwrites it, which
  // can be a couple of seconds away (see countdown_to_start) -- long
  // enough to visibly flash stale state right as a new game starts.
  // Re-rendering both prefixes now (this call doesn't know in advance
  // whether it's for a player or spectator context, and doing the
  // "wrong" one is harmless -- those elements just stay hidden until
  // actually needed) forces every panel to reflect the fresh, empty
  // state immediately instead.
  $('move-panel').classList.add('hidden');
  $('move-panel').classList.remove('pending');
  renderAuctionPanel(false);
  renderAuctionPanel(true);
  renderMyPanel();
  renderMoneyChips([]);
  // See style.css's .move-panel.has-timer rule: reserves the move-timer
  // badge's own layout space for the whole game so it starting/stopping
  // doesn't resize the panel, but only in rooms that actually have a
  // per-move timer -- an untimed room's panel never shows this badge at
  // all, so it should stay exactly as compact as it always was.
  $('move-panel').classList.toggle('has-timer', !!game.turnTimeLimit);
  applyRoomDisplaySettings();
}

// Reflects the room's fixed reveal-cards/show-logs settings in the UI: a
// read-only status label (there's no toggle anymore — see resetGameState)
// and hiding the *player* log panel when the host turned it off. Spectators
// always get the log regardless of that setting — they have no toasts/
// opponent-panel context of their own to fall back on, so it's their main
// way to follow what's happening, not an optional extra like it is for
// players.
export function applyRoomDisplaySettings() {
  const label = game.revealCards ? 'Cards revealed' : 'Cards hidden';
  $('reveal-cards-status').textContent = label;
  $('spec-reveal-cards-status').textContent = label;
  $('game-log').closest('details').classList.toggle('hidden', !game.showLogs);

  const seedLabel = game.manualSeed && game.seed != null ? `(Seed: ${game.seed})` : '';
  $('seed-display').textContent = seedLabel;
  $('spec-seed-display').textContent = seedLabel;
}

export function seedOpponents(status, myUsername) {
  (status.joined || []).forEach((p) => {
    if (p.username === myUsername) return;
    game.opponents[p.username] = { name: p.name, statusCards: [], active: true, outOfAuction: false, lastBid: null };
  });
}

export function ensureOpponent(username) {
  if (!game.opponents[username]) {
    game.opponents[username] = { name: username, statusCards: [], active: true, outOfAuction: false, lastBid: null };
  }
  return game.opponents[username];
}

// Points formula mirrors BasePlayer.__calculate_points(): sum of values,
// times the product of multipliers (Passe: -5/×1, Scandale: 0/×0.5,
// Prestige: 0/×2) — see components_module/{disgrace_card,prestige_card}.py.
export function computePoints(statusCards) {
  let sum = 0;
  let mult = 1;
  for (const c of statusCards) { sum += c.value; mult *= c.multiplier; }
  return sum * mult;
}

// The one place a fresh, answerable prompt for THIS player gets opened --
// called only from gameEvents.js's applyPlayerMove. Owns the staleness
// check, the self-healing turnPlayer correction, and the panel render;
// nothing else may set game.myPrompt directly. Returns false (having done
// nothing) for a stale re-send of an already-answered prompt.
export function openMyPrompt(moveSeq) {
  if (moveSeq != null && game.highestAnsweredMoveSeq != null && moveSeq <= game.highestAnsweredMoveSeq) {
    // A stale re-send, over the network, of the exact prompt already
    // answered -- our own answer simply hasn't reached/been processed by
    // the server yet, or crossed this message in flight. Applying it
    // anyway would re-open an already-answered, already-greyed move panel
    // right after the player acted on it -- a real, live-reproduced bug
    // ("I placed my bid, panel didn't grey out"). Ignore entirely; the
    // panel is already correct.
    return false;
  }
  game.myPrompt = { moveSeq, answered: false };
  // Self-healing guarantee, independent of any other message: receiving a
  // PLAYER_MOVE is unambiguous proof it's this player's own turn right now.
  // Closes a real, live-reproduced bug where the bid panel opened correctly
  // (this message got through) while the header above it stayed on an
  // earlier round/player. game.myPrompt (just set above) is now the
  // label's primary "Your turn" signal (see renderAuctionPanel), so this
  // write is redundant with it in the normal case -- kept for
  // renderOpponents' opponent-highlighting either way, and as a harmless
  // fallback for the brief pre-reconnect window renderAuctionPanel's own
  // comment describes.
  game.turnPlayer = game.myUsername;
  renderMovePanel();
  return true;
}

// The one place an answer to game.myPrompt gets marked as sent -- called
// by gameActions.js's onPlaceBid/onPass/onDiscardPainting right before
// ws.send(), and by gameRenderer.js's updateMoveTimerDisplay auto-pass-on-
// timeout path. Returns false (having done nothing) if there's no open
// prompt to answer.
export function answerMyPrompt() {
  if (!game.myPrompt || game.myPrompt.answered) return false;
  game.myPrompt.answered = true;
  // Recorded here (not a separate variable) so openMyPrompt's staleness
  // check survives this exact prompt being replaced by the next one.
  game.highestAnsweredMoveSeq = game.myPrompt.moveSeq;
  clearMoveTimer(); // acted — no need to keep counting down what's already submitted
  $('move-error').classList.add('hidden');
  // The panel is now correctly blocked, but the server's broadcast of what
  // actually happens next (whose turn it really is) hasn't arrived yet --
  // without this, game.turnPlayer keeps pointing at whoever just acted
  // (often *this* player), so the auction header kept reading "Your turn"
  // and the wrong opponent stayed highlighted for however long that
  // round-trip took. Blank/neutral here is honest about "we don't know
  // yet"; stale is actively misleading.
  game.turnPlayer = null;
  renderMovePanel();
  return true;
}
