import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { ProfileProvider, useProfile } from './ProfileContext';

describe('ProfileContext', () => {
  beforeEach(() => {
    localStorage.clear();
    document.cookie = 'hs_profile=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
  });

  it('starts with no profile when nothing is stored', () => {
    const { result } = renderHook(() => useProfile(), { wrapper: ProfileProvider });
    expect(result.current.profile).toBeNull();
  });

  it('persists a saved profile to localStorage and reflects it immediately', () => {
    const { result } = renderHook(() => useProfile(), { wrapper: ProfileProvider });
    act(() => result.current.saveProfile('alice', 'alice', null));
    expect(result.current.profile).toEqual({ username: 'alice', name: 'alice', googleId: null });
    expect(JSON.parse(localStorage.getItem('hs_profile')!)).toEqual({
      username: 'alice', name: 'alice', google_id: null,
    });
  });

  it('a later save that omits googleId keeps whatever was already linked', () => {
    // Mirrors the old auth/profile.js contract: editing just the display
    // name (e.g. a username change) must not silently un-link a real
    // Google account by defaulting googleId to null.
    const { result } = renderHook(() => useProfile(), { wrapper: ProfileProvider });
    act(() => result.current.saveProfile('bob', 'bob', 'google-sub-123'));
    act(() => result.current.saveProfile('bobby', 'bobby'));
    expect(result.current.profile).toEqual({ username: 'bobby', name: 'bobby', googleId: 'google-sub-123' });
  });

  it('logout clears the stored profile', () => {
    const { result } = renderHook(() => useProfile(), { wrapper: ProfileProvider });
    act(() => result.current.saveProfile('carol', 'carol', null));
    act(() => result.current.logout());
    expect(result.current.profile).toBeNull();
    expect(localStorage.getItem('hs_profile')).toBeNull();
  });

  it('a new mount picks up a profile someone else already stored (e.g. a previous session)', () => {
    localStorage.setItem('hs_profile', JSON.stringify({ username: 'dave', name: 'dave', google_id: null }));
    const { result } = renderHook(() => useProfile(), { wrapper: ProfileProvider });
    expect(result.current.profile).toEqual({ username: 'dave', name: 'dave', googleId: null });
  });
});
