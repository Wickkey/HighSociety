// Pure display helpers derived from GameState -- ported from the old
// frontend's game/gameRenderer.js. Framework-agnostic on purpose (plain
// functions, not hooks): components call these directly while rendering,
// the same role gameRenderer.js's exports played for the DOM-mutation code
// they replace.
import type { GameCard, GameState } from '../types/game';

export const CARD_INFO_TEXT: Record<string, string> = {
  Painting: 'Normal auction. Highest bidder wins and pays their bid. Worth its printed value in points.',
  PrestigeCard: 'Normal auction. Highest bidder wins and pays their bid. Doubles your entire final score. High stakes!',
  FauxPas: "Disgrace auction: opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Taking it means you must immediately discard a Painting you own (or your next one, if you don't have one yet).",
  Passe: 'Disgrace auction: opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Costs you 5 points.',
  Scandale: 'Disgrace auction: opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Halves your entire final score.',
};

/** "You" instead of your own username in event/log text -- reads more
 * naturally when it's your own action being announced back to you.
 * `myUsername` is null for spectators, so this always returns the real
 * name for them -- correct, since a spectator has no "my side". */
export function actorLabel(username: string, myUsername: string | null): string {
  return username === myUsername ? 'You' : username;
}

const CARD_TYPE_NAMES: Record<string, string> = {
  Painting: 'Painting', PrestigeCard: 'Prestige Card', FauxPas: 'Faux Pas', Passe: 'Passe', Scandale: 'Scandale',
};

export function cardTypeName(card: GameCard): string {
  return CARD_TYPE_NAMES[card.type] || card.type;
}

export function describeCard(card: GameCard): string {
  switch (card.type) {
    case 'Painting': return `Painting (${card.value})`;
    case 'PrestigeCard': return 'Prestige Card (×2)';
    case 'FauxPas': return 'Faux Pas';
    case 'Passe': return 'Passe (−5)';
    case 'Scandale': return 'Scandale (½×, green)';
    default: return card.type;
  }
}

/** Color coding is deliberately just green-vs-not: Prestige and Scandale are
 * the two actual "green cards", so only they get real green -- every other
 * card shares one neutral tone. */
export function cardLabel(card: GameCard): { tone: 'neutral' | 'green'; text: string } {
  switch (card.type) {
    case 'Painting': return { tone: 'neutral', text: String(card.value) };
    case 'PrestigeCard': return { tone: 'green', text: '×2' };
    case 'FauxPas': return { tone: 'neutral', text: 'Faux Pas' };
    case 'Passe': return { tone: 'neutral', text: '−5' };
    case 'Scandale': return { tone: 'green', text: '½×' };
    default: return { tone: 'neutral', text: card.type };
  }
}

/** Points formula mirrors BasePlayer.__calculate_points(): sum of values,
 * times the product of multipliers (Passe: -5/×1, Scandale: 0/×0.5,
 * Prestige: 0/×2). */
export function computePoints(statusCards: GameCard[]): number {
  let sum = 0;
  let mult = 1;
  for (const c of statusCards) { sum += c.value; mult *= c.multiplier; }
  return sum * mult;
}

/** How many seconds before zero the move-timer should turn "urgent" --
 * scaled to the room's actual per-move limit rather than a flat 5s, since
 * 5s left out of a 20s move reads very differently than 5s left out of a
 * 3-minute one. */
export function urgentWindowSeconds(turnTimeLimit: number | null): number {
  if (!turnTimeLimit || turnTimeLimit < 30) return 5;
  if (turnTimeLimit <= 180) return 15;
  return 30;
}

/** Puts the opponent list in real seat/turn order (once known) instead of
 * whichever order each player was first heard about, rotated to start right
 * after "me" for players so the list always reads top-to-bottom in the
 * exact order turns will actually advance. Spectators have no seat of their
 * own to rotate around, so they just see the raw seat order start to
 * finish. */
export function orderedOpponentUsernames(state: GameState, isSpectator: boolean): string[] {
  const known = Object.keys(state.opponents);
  if (!state.playerOrder.length) return known; // player_order hasn't arrived yet

  let order = state.playerOrder;
  if (!isSpectator && state.myUsername) {
    const myIdx = order.indexOf(state.myUsername);
    if (myIdx !== -1) order = order.slice(myIdx + 1).concat(order.slice(0, myIdx));
  }
  const result = order.filter((u) => u !== state.myUsername && known.includes(u));
  known.forEach((u) => { if (!result.includes(u)) result.push(u); }); // defensive fallback only
  return result;
}
