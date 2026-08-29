import { describe, expect, it } from 'vitest';
import { createInitialGameState, gameReducer, type GameAction } from './gameReducer';
import type { GameState } from '../types/game';

function reduce(state: GameState, ...actions: GameAction[]): GameState {
  return actions.reduce(gameReducer, state);
}

const auctionStart = (round: number, startingPlayer: string) => ({
  message_type: 'AUCTION_UPDATE',
  data: { round_number: round, kind: 'auction_start', card: { type: 'Painting', value: 4, multiplier: 1, is_green: false }, starting_player: startingPlayer },
} as const);

const playerMove = (moveSeq: number, moveType: 'bid' | 'discard_painting' = 'bid') => ({
  message_type: 'PLAYER_MOVE',
  move_type: moveType,
  data: { move_seq: moveSeq },
  constraints: moveType === 'discard_painting' ? { allowed_paintings: [3, 5] } : { allowed_money_cards: [1, 2, 3] },
} as const);

describe('gameReducer', () => {
  it('a stale PLAYER_MOVE resend of an already-answered prompt is ignored entirely', () => {
    let state = createInitialGameState('alice');
    state = reduce(state, { type: 'SERVER_MESSAGE', message: playerMove(5) }, { type: 'PASS_SUBMITTED' });
    expect(state.myPrompt).toEqual({ moveSeq: 5, answered: true });

    const before = state;
    state = reduce(state, { type: 'SERVER_MESSAGE', message: playerMove(5) }); // same moveSeq, resent
    expect(state).toBe(before); // completely unchanged, not just logically equal
  });

  it('a stale PLAYER_MOVE_TIMER (crossed in flight with our own pass) is ignored', () => {
    let state = createInitialGameState('alice');
    state = reduce(
      state,
      { type: 'SERVER_MESSAGE', message: playerMove(3) },
      { type: 'PASS_SUBMITTED' },
    );
    expect(state.moveDeadline).toBeNull();
    state = reduce(state, {
      type: 'SERVER_MESSAGE',
      message: { message_type: 'PLAYER_MOVE_TIMER', data: { move_seq: 3, seconds_remaining: 20 } },
    });
    expect(state.moveDeadline).toBeNull(); // did NOT restart the timer for an already-answered move
  });

  it('answering a prompt blanks turnPlayer rather than leaving it stale', () => {
    let state = createInitialGameState('alice');
    state = reduce(state, { type: 'SERVER_MESSAGE', message: playerMove(1) });
    expect(state.turnPlayer).toBe('alice');
    state = reduce(state, { type: 'BID_SUBMITTED' });
    expect(state.turnPlayer).toBeNull();
  });

  it('BID_SUBMITTED clears the selected bid but PASS_SUBMITTED on a no-op prompt does not touch it', () => {
    let state = createInitialGameState('alice');
    state = reduce(
      state,
      { type: 'SERVER_MESSAGE', message: playerMove(1) },
      { type: 'SELECT_MONEY_CARD', value: 5 },
    );
    expect(state.selectedBid).toEqual([5]);
    state = reduce(state, { type: 'BID_SUBMITTED' });
    expect(state.selectedBid).toEqual([]);

    // Answering again (nothing open) is a no-op that must not clear an
    // unrelated future selection out from under the next prompt.
    const before = reduce(state, { type: 'SERVER_MESSAGE', message: playerMove(2) }, { type: 'SELECT_MONEY_CARD', value: 9 });
    const after = reduce(before, { type: 'PASS_SUBMITTED' }); // 'bid' move type, but exercising PASS_SUBMITTED's shared no-op path is what matters here
    expect(after).not.toBe(before); // it DID answer the open prompt...
    expect(after.selectedBid).toEqual([9]); // ...but PASS_SUBMITTED (unlike BID_SUBMITTED) never touches selectedBid
  });

  it('auction_start resets every opponent\'s outOfAuction/lastBid from the previous round', () => {
    let state = createInitialGameState('alice');
    state = reduce(
      state,
      { type: 'SEED_OPPONENTS', joined: [{ username: 'bob', name: 'bob' }, { username: 'carol', name: 'carol' }] },
      { type: 'SERVER_MESSAGE', message: { message_type: 'AUCTION_UPDATE', data: { round_number: 1, kind: 'pass', player: 'bob' } } },
    );
    expect(state.opponents.bob.outOfAuction).toBe(true);
    state = reduce(state, { type: 'SERVER_MESSAGE', message: auctionStart(2, 'carol') });
    expect(state.opponents.bob.outOfAuction).toBe(false);
    expect(state.opponents.carol.outOfAuction).toBe(false);
    expect(state.myAuctionBid).toBe(0);
    expect(state.maxBid).toBe(0);
  });

  it('opponent_state_sync (reconnect catch-up) restores an opponent silently, no log line', () => {
    let state = createInitialGameState('alice');
    state = reduce(state, {
      type: 'SERVER_MESSAGE',
      message: {
        message_type: 'GLOBAL_EVENT',
        prompt: '',
        data: { event: 'opponent_state_sync', username: 'bob', name: 'bob', active: true, status_cards: [{ type: 'Painting', value: 7, multiplier: 1, is_green: false }] },
      },
    });
    expect(state.opponents.bob.statusCards).toHaveLength(1);
    expect(state.log).toHaveLength(0);
  });

  it('a rejected bid (INPUT_ERROR) reopens the same prompt for another attempt', () => {
    let state = createInitialGameState('alice');
    state = reduce(state, { type: 'SERVER_MESSAGE', message: playerMove(1) }, { type: 'BID_SUBMITTED' });
    expect(state.myPrompt?.answered).toBe(true);
    state = reduce(state, { type: 'SERVER_MESSAGE', message: { message_type: 'INPUT_ERROR', prompt: 'Bid too low.' } });
    expect(state.myPrompt?.answered).toBe(false);
    expect(state.moveError).toBe('Bid too low.');
  });

  it('a discard prompt clears any leftover bid error and never carries a timer', () => {
    let state = createInitialGameState('alice');
    state = reduce(state, { type: 'SERVER_MESSAGE', message: { message_type: 'INPUT_ERROR', prompt: 'stale error' } });
    state = reduce(state, {
      type: 'SERVER_MESSAGE',
      message: { message_type: 'PLAYER_MOVE_TIMER', data: { move_seq: null, seconds_remaining: 15 } },
    });
    expect(state.moveDeadline).not.toBeNull();
    state = reduce(state, { type: 'SERVER_MESSAGE', message: playerMove(2, 'discard_painting') });
    expect(state.moveError).toBeNull();
    expect(state.moveDeadline).toBeNull();
    expect(state.allowedPaintings).toEqual([3, 5]);
  });

  it('RESET rebuilds the entire state in one dispatch, wiping the previous game', () => {
    let state = createInitialGameState('alice');
    state = reduce(state, { type: 'SERVER_MESSAGE', message: auctionStart(3, 'alice') });
    expect(state.round).toBe(3);
    state = reduce(state, { type: 'RESET', myUsername: 'alice', roomSettings: { turnTimeLimit: 30 } });
    expect(state.round).toBe(0);
    expect(state.opponents).toEqual({});
    expect(state.turnTimeLimit).toBe(30);
  });
});
