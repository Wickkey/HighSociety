import { beforeEach, describe, expect, it } from 'vitest';
import { clearRejoinInfo, loadRejoinInfo, saveRejoinInfo } from './rejoin';

describe('rejoin info storage', () => {
  beforeEach(() => localStorage.clear());

  it('round-trips what was saved, scoped per room code', () => {
    saveRejoinInfo('ABCDE', 'tok-1', 'alice', 'Alice');
    expect(loadRejoinInfo('ABCDE')).toEqual({ token: 'tok-1', username: 'alice', name: 'Alice' });
    expect(loadRejoinInfo('OTHER')).toBeNull();
  });

  it('returns null when nothing is stored', () => {
    expect(loadRejoinInfo('ABCDE')).toBeNull();
  });

  it('returns null for corrupt/incomplete stored data rather than throwing', () => {
    localStorage.setItem('hs_rejoin_ABCDE', 'not json');
    expect(loadRejoinInfo('ABCDE')).toBeNull();
    localStorage.setItem('hs_rejoin_ABCDE', JSON.stringify({ username: 'alice' })); // no token
    expect(loadRejoinInfo('ABCDE')).toBeNull();
  });

  it('clear removes only that room\'s entry', () => {
    saveRejoinInfo('ABCDE', 'tok-1', 'alice', 'Alice');
    saveRejoinInfo('OTHER', 'tok-2', 'bob', 'Bob');
    clearRejoinInfo('ABCDE');
    expect(loadRejoinInfo('ABCDE')).toBeNull();
    expect(loadRejoinInfo('OTHER')).toEqual({ token: 'tok-2', username: 'bob', name: 'Bob' });
  });
});
