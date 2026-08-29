import { describe, expect, it } from 'vitest';
import { resolveIdentifyAnswer } from './protocol';

describe('resolveIdentifyAnswer', () => {
  const identity = { username: 'alice_acct', name: 'Alice' };

  it('answers with the account username when the prompt asks for one', () => {
    expect(resolveIdentifyAnswer('Enter your username', identity)).toBe('alice_acct');
    expect(resolveIdentifyAnswer('USERNAME?', identity)).toBe('alice_acct');
  });

  it('answers with the display name for any other prompt', () => {
    expect(resolveIdentifyAnswer('Enter your display name', identity)).toBe('Alice');
    expect(resolveIdentifyAnswer('Name?', identity)).toBe('Alice');
  });
});
