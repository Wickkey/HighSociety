// Shared shapes for live game state -- mirrors the wire format gameplay.py
// actually sends (see the old frontend's game/gameState.js), not a redesign.

export interface GameCard {
  type: 'Painting' | 'PrestigeCard' | 'FauxPas' | 'Passe' | 'Scandale' | string;
  value: number;
  multiplier: number;
  is_green: boolean;
  description?: string;
}

export interface OpponentState {
  name: string;
  statusCards: GameCard[];
  active: boolean;
  outOfAuction: boolean;
  lastBid: number | null;
}

/** The one source of truth for "do I currently have an open decision, and
 * have I already answered it" -- see gameReducer.ts's OPEN_MY_PROMPT/
 * ANSWER_MY_PROMPT, ported from the old frontend's openMyPrompt/
 * answerMyPrompt in game/gameState.js. */
export interface MyPrompt {
  moveSeq: number | null;
  answered: boolean;
}

export type MoveType = 'bid' | 'discard_painting' | null;

export interface LogEntry {
  id: number;
  text: string;
}

export interface GameState {
  round: number;
  card: GameCard | null;
  maxBid: number;
  /** This player's own cumulative committed bid for the *current* auction
   * only. Meaningless (left at 0) for spectators -- they have no bid of
   * their own. */
  myAuctionBid: number;
  turnPlayer: string | null;
  turnStartedAt: number | null;
  /** null for a spectator -- see actorLabel's old "You" vs real-name split. */
  myUsername: string | null;
  myPoints: number;
  myStatusCards: GameCard[];
  myMoneyCards: number[];
  selectedBid: number[];
  myPrompt: MyPrompt | null;
  highestAnsweredMoveSeq: number | null;
  moveType: MoveType;
  allowedPaintings: number[];
  selectedDiscardValue: number | null;
  /** Wall-clock ms timestamp the current per-move countdown expires at, or
   * null when there's no live countdown (untimed room, no open prompt, or a
   * discard prompt -- those never carry a timer). Recomputed fresh from
   * each PLAYER_MOVE_TIMER's `seconds_remaining` rather than ticked down by
   * the reducer itself -- see hooks/useMoveTimer.ts for the actual countdown. */
  moveDeadline: number | null;
  moveError: string | null;
  opponents: Record<string, OpponentState>;
  playerOrder: string[];
  revealCards: boolean;
  showLogs: boolean;
  turnTimeLimit: number | null;
  seed: number | null;
  manualSeed: boolean;
  resigned: boolean;
  spectatorCount: number | null;
  log: LogEntry[];
  chat: LogEntry[];
}
