// The live game's state machine -- ported from the old frontend's
// game/gameState.js (the `game` object + resetGameState/openMyPrompt/
// answerMyPrompt) and game/gameEvents.js (applyGameMessage and friends).
// A plain reducer replaces that file pair's imperative "mutate `game`,
// then call the matching render function" pattern: every case here returns
// a new state, and every component re-renders from it -- there is no
// separate step to remember to call.
//
// Deliberately out of scope for this pass (kept working via the log's
// plain text instead): the transient event-toast queue, quick-reaction
// bubbles, the countdown/final-green overlays, and the move-timer's urgent
// double-beep -- all purely decorative on top of state that's already
// correct here, not functionality of their own. Revisit once the core
// auction loop above them has been live-tested.
import type { GameCard, GameState, MoveType, OpponentState } from '../types/game';
import type { GenericGameMessage, RematchMessage } from '../ws/protocol';

export function createInitialGameState(
  myUsername: string | null,
  roomSettings: {
    revealCards?: boolean; showLogs?: boolean; turnTimeLimit?: number | null; seed?: number | null; manualSeed?: boolean;
  } = {},
): GameState {
  return {
    round: 0,
    card: null,
    maxBid: 0,
    myAuctionBid: 0,
    turnPlayer: null,
    turnStartedAt: null,
    myUsername,
    myPoints: 0,
    myStatusCards: [],
    myMoneyCards: [],
    selectedBid: [],
    myPrompt: null,
    highestAnsweredMoveSeq: null,
    moveType: null,
    allowedPaintings: [],
    selectedDiscardValue: null,
    moveDeadline: null,
    moveError: null,
    opponents: {},
    playerOrder: [],
    revealCards: roomSettings.revealCards !== false,
    showLogs: roomSettings.showLogs !== false,
    turnTimeLimit: roomSettings.turnTimeLimit ?? null,
    seed: roomSettings.seed ?? null,
    manualSeed: !!roomSettings.manualSeed,
    resigned: false,
    spectatorCount: null,
    log: [],
    chat: [],
  };
}

export type GameAction =
  | { type: 'RESET'; myUsername: string | null; roomSettings?: Parameters<typeof createInitialGameState>[1] }
  | { type: 'SEED_OPPONENTS'; joined: { username: string; name: string }[] }
  | { type: 'SERVER_MESSAGE'; message: GenericGameMessage | RematchMessage }
  | { type: 'SELECT_MONEY_CARD'; value: number }
  | { type: 'SELECT_DISCARD_PAINTING'; value: number }
  | { type: 'BID_SUBMITTED' }
  | { type: 'PASS_SUBMITTED' }
  | { type: 'DISCARD_SUBMITTED' }
  | { type: 'MOVE_TIMER_EXPIRED' }
  | { type: 'RESIGNED' }
  | { type: 'CHAT_SENT'; text: string };

// -------------------------------------------------------------- helpers --

function withOpponent(opponents: Record<string, OpponentState>, username: string): Record<string, OpponentState> {
  if (opponents[username]) return opponents;
  return { ...opponents, [username]: { name: username, statusCards: [], active: true, outOfAuction: false, lastBid: null } };
}

function updateOpponent(
  opponents: Record<string, OpponentState>, username: string, patch: Partial<OpponentState>,
): Record<string, OpponentState> {
  const withIt = withOpponent(opponents, username);
  return { ...withIt, [username]: { ...withIt[username], ...patch } };
}

function mapOpponents(opponents: Record<string, OpponentState>, fn: (o: OpponentState) => OpponentState): Record<string, OpponentState> {
  const result: Record<string, OpponentState> = {};
  for (const [k, v] of Object.entries(opponents)) result[k] = fn(v);
  return result;
}

function appendLog(state: GameState, text: string): GameState {
  if (!text) return state;
  return { ...state, log: [...state.log, { id: state.log.length, text }] };
}

function appendChat(state: GameState, text: string): GameState {
  return { ...state, chat: [...state.chat, { id: state.chat.length, text }] };
}

function describeCardInline(card: GameCard): string {
  switch (card.type) {
    case 'Painting': return `Painting (${card.value})`;
    case 'PrestigeCard': return 'Prestige Card (×2)';
    case 'FauxPas': return 'Faux Pas';
    case 'Passe': return 'Passe (−5)';
    case 'Scandale': return 'Scandale (½×, green)';
    default: return card.type;
  }
}

/** The one place an answer to state.myPrompt gets marked as sent -- shared
 * by BID/PASS/DISCARD_SUBMITTED and MOVE_TIMER_EXPIRED (an auto-pass on
 * timeout is, from the state machine's point of view, just another way a
 * prompt gets answered). Returns `state` unchanged (same reference) if
 * there's nothing open to answer, so callers can tell a no-op apart from a
 * real change. */
function answerPrompt(state: GameState): GameState {
  if (!state.myPrompt || state.myPrompt.answered) return state;
  return {
    ...state,
    myPrompt: { ...state.myPrompt, answered: true },
    highestAnsweredMoveSeq: state.myPrompt.moveSeq,
    moveDeadline: null,
    moveError: null,
    // The server's broadcast of what happens next hasn't arrived yet --
    // blank/neutral here is honest about "we don't know yet"; leaving the
    // old value would keep the auction header reading "Your turn" (or
    // highlighting the wrong opponent) for however long that round-trip
    // takes.
    turnPlayer: null,
  };
}

// Narration lines gameplay.py already broadcasts a plain-text GLOBAL_EVENT
// prompt for right next to a structured event this reducer separately logs
// with more detail (a bid/pass/quit/auction-win) -- skip re-logging those
// specific lines so the log doesn't show every event twice. Coupled to the
// exact wording/emoji gameplay.py uses today.
const DUPLICATE_NARRATION_PATTERNS = [
  /^Auctioning:/,
  /^💀 Disgrace Auction started for:/,
  /^💰 /,
  /^⚪ /,
  /^❌ /,
  /^💢 /,
  /wins the auction for/,
];
function isDuplicateOfStructuredEvent(text: string): boolean {
  return DUPLICATE_NARRATION_PATTERNS.some((re) => re.test(text.trim()));
}

// ------------------------------------------------------- server message --

function applyGlobalEvent(state: GameState, msg: GenericGameMessage): GameState {
  const d = msg.data as Record<string, unknown> | undefined;
  let next = state;

  if (d?.event === 'faux_pas_discard') {
    const player = d.player as string;
    if (player !== state.myUsername) {
      const opponents = withOpponent(state.opponents, player);
      const o = opponents[player];
      next = {
        ...state,
        opponents: { ...opponents, [player]: { ...o, statusCards: o.statusCards.filter((c) => c.value !== d.discarded_value) } },
      };
    }
  } else if (d?.event === 'opponent_state_sync') {
    // Reconnect catch-up only (see web_server.py's _send_reconnect_catchup)
    // -- restores what this opponent's panel should already show.
    const username = d.username as string;
    if (username !== state.myUsername) {
      next = {
        ...state,
        opponents: updateOpponent(state.opponents, username, {
          name: d.name as string, active: d.active as boolean, statusCards: d.status_cards as GameCard[],
        }),
      };
    }
  } else if (d?.event === 'player_resigned') {
    const player = d.player as string;
    if (player !== state.myUsername) next = { ...state, opponents: updateOpponent(state.opponents, player, { active: false }) };
  } else if (d?.event === 'player_reconnected') {
    const player = d.player as string;
    if (player !== state.myUsername) next = { ...state, opponents: updateOpponent(state.opponents, player, { active: true }) };
  } else if (d?.event === 'player_order') {
    next = { ...state, playerOrder: d.usernames as string[] };
  } else if (d?.event === 'spectator_count') {
    next = { ...state, spectatorCount: d.count as number };
  }
  // countdown/countdown_finished/game_over/green_card_revealed: no state
  // mutation here -- see this file's top comment on what's deferred, and
  // RoomContext's own status poll for how the game-over transition
  // actually happens without needing a signal from this socket at all.

  if (msg.prompt && !isDuplicateOfStructuredEvent(msg.prompt)) next = appendLog(next, msg.prompt.trim());
  return next;
}

function applyAuctionUpdate(state: GameState, msg: GenericGameMessage): GameState {
  const d = msg.data as Record<string, unknown>;
  let next: GameState = {
    ...state,
    round: d.round_number as number,
    card: d.card as GameCard,
    maxBid: typeof d.max_bid === 'number' ? d.max_bid : state.maxBid,
  };

  switch (d.kind) {
    case 'auction_start': {
      const startingPlayer = d.starting_player as string;
      next.maxBid = 0;
      next.myAuctionBid = 0;
      next.turnPlayer = startingPlayer;
      next.turnStartedAt = Date.now();
      next.opponents = mapOpponents(
        startingPlayer !== state.myUsername ? withOpponent(next.opponents, startingPlayer) : next.opponents,
        (o) => ({ ...o, outOfAuction: false, lastBid: null }),
      );
      next = appendLog(next, `🃏 Auction #${d.round_number}: ${describeCardInline(d.card as GameCard)}`);
      break;
    }
    case 'turn_start': {
      const player = d.player as string;
      next.turnPlayer = player;
      next.turnStartedAt = Date.now();
      if (player !== state.myUsername) next.opponents = withOpponent(next.opponents, player);
      break;
    }
    case 'bid': {
      const player = d.player as string;
      const maxBid = d.max_bid as number;
      if (player === state.myUsername) {
        next.myAuctionBid = maxBid;
      } else {
        next.opponents = updateOpponent(next.opponents, player, { lastBid: maxBid });
      }
      next = appendLog(next, `💰 ${player} raised to ${maxBid}`);
      break;
    }
    case 'pass':
    case 'fold': {
      const player = d.player as string;
      if (player !== state.myUsername) next.opponents = updateOpponent(next.opponents, player, { outOfAuction: true, lastBid: null });
      next = appendLog(next, `⚪ ${player} passed`);
      break;
    }
    case 'quit': {
      const player = d.player as string;
      if (player !== state.myUsername) next.opponents = updateOpponent(next.opponents, player, { active: false, outOfAuction: true, lastBid: null });
      next = appendLog(next, `❌ ${player} quit`);
      break;
    }
    case 'sync': {
      // Reconnect catch-up only -- a silent state restore, no log line.
      const turnPlayer = (d.turn_player as string | null) ?? null;
      next.turnPlayer = turnPlayer;
      if (turnPlayer && turnPlayer !== state.myUsername) next.opponents = withOpponent(next.opponents, turnPlayer);
      break;
    }
    default:
      break;
  }
  return next;
}

function applyAuctionResult(state: GameState, msg: GenericGameMessage): GameState {
  const d = msg.data as Record<string, unknown>;
  const card = d.card as GameCard;
  if (d.recipient) {
    const recipient = d.recipient as string;
    let opponents = state.opponents;
    if (recipient !== state.myUsername) {
      const withIt = withOpponent(opponents, recipient);
      opponents = { ...withIt, [recipient]: { ...withIt[recipient], statusCards: [...withIt[recipient].statusCards, card] } };
    }
    const moneySpent = d.money_spent as Record<string, number> | undefined;
    const spent = moneySpent?.[recipient] ?? 0;
    return appendLog({ ...state, opponents }, `🏆 ${recipient} won ${describeCardInline(card)} for ${spent}`);
  }
  return appendLog(state, `⚠️ Nobody took ${describeCardInline(card)}`);
}

function applyPlayerState(state: GameState, msg: GenericGameMessage): GameState {
  const d = msg.data as Record<string, unknown>;
  const next: GameState = { ...state, myPoints: d.points as number, myStatusCards: d.status_cards as GameCard[] };
  if (Array.isArray(d.money_cards)) next.myMoneyCards = d.money_cards as number[];
  return next;
}

function applyPlayerMove(state: GameState, msg: GenericGameMessage & { move_type?: string; constraints?: Record<string, unknown> }): GameState {
  const moveSeq = (msg.data as Record<string, unknown> | undefined)?.move_seq as number | undefined ?? null;
  if (moveSeq != null && state.highestAnsweredMoveSeq != null && moveSeq <= state.highestAnsweredMoveSeq) {
    // A stale re-send of an already-answered prompt -- see
    // answerPrompt's own comment for why applying it anyway would
    // re-open an already-greyed panel right after the player acted on it.
    return state;
  }
  const moveType: MoveType = msg.move_type === 'discard_painting' ? 'discard_painting' : 'bid';
  const next: GameState = {
    ...state,
    myPrompt: { moveSeq, answered: false },
    turnPlayer: state.myUsername, // self-healing: a fresh prompt is unambiguous proof it's our turn now
    moveType,
  };
  if (moveType === 'discard_painting') {
    next.allowedPaintings = (msg.constraints?.allowed_paintings as number[] | undefined) ?? [];
    next.moveDeadline = null; // discard prompts never carry a per-move timer
    next.moveError = null; // no bid-error concept for discard -- safe to clear any leftover
  } else {
    next.selectedBid = [];
    if (Array.isArray(msg.constraints?.allowed_money_cards)) next.myMoneyCards = msg.constraints!.allowed_money_cards as number[];
    // Deliberately NOT clearing moveError here -- this same branch renders
    // the very next bid prompt immediately after a rejected bid, and the
    // error should stay visible until the player does something new.
  }
  return next;
}

function applyServerMessage(state: GameState, msg: GenericGameMessage | RematchMessage): GameState {
  switch (msg.message_type) {
    case 'GLOBAL_EVENT': return applyGlobalEvent(state, msg as GenericGameMessage);
    case 'AUCTION_UPDATE': return applyAuctionUpdate(state, msg as GenericGameMessage);
    case 'AUCTION_RESULT': return applyAuctionResult(state, msg as GenericGameMessage);
    case 'PLAYER_STATE': return state.myUsername ? applyPlayerState(state, msg as GenericGameMessage) : state; // spectators never receive this, but stay defensive
    case 'PLAYER_MOVE': return state.myUsername ? applyPlayerMove(state, msg as GenericGameMessage & { move_type?: string; constraints?: Record<string, unknown> }) : state;
    case 'PLAYER_MOVE_TIMER': {
      const d = (msg as GenericGameMessage).data as Record<string, unknown> | undefined;
      const moveSeq = d?.move_seq as number | undefined ?? null;
      const isStale = moveSeq != null && state.highestAnsweredMoveSeq != null && moveSeq <= state.highestAnsweredMoveSeq;
      if (isStale || typeof d?.seconds_remaining !== 'number') return state;
      return { ...state, moveDeadline: Date.now() + d.seconds_remaining * 1000 };
    }
    case 'INPUT_ERROR': {
      const prompt = (msg as GenericGameMessage).prompt as string;
      return {
        ...state,
        moveError: prompt,
        // Reopen the same still-live decision for another attempt -- see
        // applyPlayerMove's identical reasoning for why this is the same
        // prompt, not a new one.
        myPrompt: state.myPrompt ? { ...state.myPrompt, answered: false } : state.myPrompt,
      };
    }
    case 'CHAT': return appendChat(state, (msg as GenericGameMessage).prompt as string);
    default:
      return state; // REMATCH_*: Phase 4's concern; GLOBAL_MOVE_INFO/PLAYER_INFO: superseded by the structured messages above
  }
}

// ------------------------------------------------------------- reducer --

export function gameReducer(state: GameState, action: GameAction): GameState {
  switch (action.type) {
    case 'RESET':
      return createInitialGameState(action.myUsername, action.roomSettings);

    case 'SEED_OPPONENTS': {
      let opponents = state.opponents;
      for (const p of action.joined) {
        if (p.username === state.myUsername) continue;
        opponents = { ...opponents, [p.username]: { name: p.name, statusCards: [], active: true, outOfAuction: false, lastBid: null } };
      }
      return { ...state, opponents };
    }

    case 'SERVER_MESSAGE':
      return applyServerMessage(state, action.message);

    case 'SELECT_MONEY_CARD': {
      const selectedBid = state.selectedBid.includes(action.value)
        ? state.selectedBid.filter((v) => v !== action.value)
        : [...state.selectedBid, action.value];
      return { ...state, selectedBid };
    }

    case 'SELECT_DISCARD_PAINTING':
      return { ...state, selectedDiscardValue: action.value };

    case 'BID_SUBMITTED': {
      const answered = answerPrompt(state);
      // Once sent, these chips are no longer "being added on top" -- they're
      // already part of the committed bid. The server's own echo of this
      // bid (a fresh AUCTION_UPDATE) updates myAuctionBid to the new
      // committed total, so clearing the selection here avoids double-
      // counting it on top of that.
      return answered === state ? state : { ...answered, selectedBid: [] };
    }

    case 'PASS_SUBMITTED':
    case 'DISCARD_SUBMITTED':
    case 'MOVE_TIMER_EXPIRED':
      return answerPrompt(state);

    case 'RESIGNED':
      return { ...state, resigned: true, myPrompt: null, turnPlayer: null, moveDeadline: null };

    case 'CHAT_SENT':
      // CHAT is never echoed back to its own sender over the wire -- append
      // it locally, formatted the same as an incoming one, so "did that
      // actually send?" is never a question.
      return appendChat(state, `You: ${action.text}`);

    default:
      return state;
  }
}
