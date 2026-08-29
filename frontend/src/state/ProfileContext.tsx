// Persistent device identity -- ported from the old frontend's
// auth/profile.js (localStorage, with a cookie fallback for private-mode
// browsers that block localStorage but still allow cookies). Plain
// useState is enough here (unlike GameContext): identity changes at most
// once or twice per session (login, a username edit), never at
// WebSocket-message frequency.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { Profile } from '../types/profile';

const STORAGE_KEY = 'hs_profile';

function readCookie(key: string): string | null {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function writeCookie(key: string, value: string, days: number): void {
  try {
    const expires = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toUTCString();
    document.cookie = `${key}=${encodeURIComponent(value)}; expires=${expires}; path=/; samesite=lax`;
  } catch {
    // Cookies disabled/blocked -- localStorage (the primary copy) still works.
  }
}

function readStoredProfile(): Profile | null {
  let raw: string | null = null;
  try { raw = localStorage.getItem(STORAGE_KEY); } catch { /* private mode, etc. */ }
  if (!raw) raw = readCookie(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || !parsed.username || !parsed.name) return null;
    return { username: parsed.username, name: parsed.name, googleId: parsed.google_id ?? null };
  } catch {
    return null;
  }
}

function writeStoredProfile(profile: Profile): void {
  const value = JSON.stringify({ username: profile.username, name: profile.name, google_id: profile.googleId });
  try { localStorage.setItem(STORAGE_KEY, value); } catch { /* fall through to the cookie */ }
  writeCookie(STORAGE_KEY, value, 365);
}

interface ProfileContextValue {
  profile: Profile | null;
  /** Saves and adopts a new/updated identity. `googleId` omitted keeps
   * whatever was already stored (e.g. editing just the display name
   * shouldn't un-link a real Google account); pass null explicitly for a
   * guest profile with no linked account at all. */
  saveProfile: (username: string, name: string, googleId?: string | null) => void;
  logout: () => void;
}

const ProfileContext = createContext<ProfileContextValue | null>(null);

export function ProfileProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<Profile | null>(() => readStoredProfile());

  const save = useCallback((username: string, name: string, googleId?: string | null) => {
    setProfile((existing) => {
      const resolvedGoogleId = googleId !== undefined ? googleId : (existing?.googleId ?? null);
      const next: Profile = { username, name, googleId: resolvedGoogleId };
      writeStoredProfile(next);
      return next;
    });
  }, []);

  const logout = useCallback(() => {
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* private mode, etc. */ }
    writeCookie(STORAGE_KEY, '', -1);
    setProfile(null);
  }, []);

  const value = useMemo(() => ({ profile, saveProfile: save, logout }), [profile, save, logout]);
  return <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>;
}

export function useProfile(): ProfileContextValue {
  const ctx = useContext(ProfileContext);
  if (!ctx) throw new Error('useProfile must be used within a ProfileProvider');
  return ctx;
}
