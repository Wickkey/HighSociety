// Ported from the old frontend's auth/login.js -- Google sign-in (via
// GIS's own popup/One Tap UI) and the guest path, converging on the same
// "pick/confirm a username" form for a first-time visitor either way.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useAppConfig } from '../hooks/useAppConfig';
import { useGoogleReady } from '../hooks/useGoogleReady';
import { useProfile } from '../state/ProfileContext';
import styles from './Login.module.css';

function decodeJwtPayload(token: string): { sub?: string } {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(base64));
  } catch {
    return {};
  }
}

function ShuffleIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 6h3.5c2 0 3 1 4 2.5l5 8c1 1.5 2 2.5 4 2.5H21" />
      <path d="M3 18h3.5c2 0 3-1 4-2.5" />
      <polyline points="18 3 21 6 18 9" />
      <polyline points="18 15 21 18 18 21" />
    </svg>
  );
}

export function Login() {
  const config = useAppConfig();
  const googleReady = useGoogleReady();
  const { saveProfile } = useProfile();
  const buttonContainerRef = useRef<HTMLDivElement>(null);

  const [showUsernameForm, setShowUsernameForm] = useState(false);
  const [username, setUsername] = useState('');
  const [error, setError] = useState('');
  // The Google ID token from step 1, held only while step 2 (claiming a
  // username for a brand-new Google account) is in progress -- null means
  // "this is the guest path instead" for onContinue below.
  const pendingGoogleTokenRef = useRef<string | null>(null);

  useEffect(() => {
    if (!config?.googleClientId || !googleReady || !buttonContainerRef.current) return;
    window.google!.accounts.id.initialize({
      client_id: config.googleClientId,
      callback: async (response) => {
        setError('');
        try {
          const result = await api.authGoogle(response.credential);
          if (result.needs_username) {
            pendingGoogleTokenRef.current = response.credential;
            setUsername(result.suggested_display_name || '');
            setShowUsernameForm(true);
          } else {
            saveProfile(result.username!, result.username!, decodeJwtPayload(response.credential).sub ?? null);
          }
        } catch (e) {
          pendingGoogleTokenRef.current = null;
          setShowUsernameForm(true);
          setError((e as Error).message);
        }
      },
    });
    window.google!.accounts.id.renderButton(buttonContainerRef.current, {
      theme: 'filled_black', size: 'large', width: 280, text: 'continue_with',
    });
  }, [config?.googleClientId, googleReady, saveProfile]);

  async function onContinueAsGuest() {
    pendingGoogleTokenRef.current = null;
    setError('');
    setShowUsernameForm(true);
    try {
      const { username: suggested } = await api.authGuestSuggest();
      setUsername(suggested);
    } catch {
      // Leave the field blank -- the visitor can still type their own.
    }
  }

  async function onShuffle() {
    try {
      const { username: suggested } = await api.authGuestSuggest();
      setUsername(suggested);
    } catch { /* transient -- leave whatever's already typed */ }
  }

  async function onContinue() {
    const trimmed = username.trim();
    setError('');
    if (!trimmed) { setError('Username is required.'); return; }

    try {
      if (pendingGoogleTokenRef.current) {
        const token = pendingGoogleTokenRef.current;
        const result = await api.authGoogleClaimUsername(token, trimmed, trimmed);
        saveProfile(result.username!, result.username!, decodeJwtPayload(token).sub ?? null);
        pendingGoogleTokenRef.current = null;
      } else {
        const result = await api.authGuestClaim(trimmed);
        saveProfile(result.username, result.username, null);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className={`card panel panel--centered ${styles.panel}`}>
      <h2>Welcome to High Society</h2>

      <div ref={buttonContainerRef} className={styles.googleSigninContainer} />
      {config?.googleClientId && (
        <div className={styles.divider}><span>or</span></div>
      )}

      <button type="button" className={styles.guestButton} onClick={onContinueAsGuest}>
        Continue as Guest
      </button>

      {showUsernameForm && (
        <div className={styles.usernameForm}>
          <label>
            Username
            <div className={styles.usernameRow}>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="e.g. alex"
                autoComplete="off"
                maxLength={24}
              />
              {!pendingGoogleTokenRef.current && (
                <button
                  type="button"
                  className={`secondary ${styles.shuffleButton}`}
                  title="Get a different name"
                  aria-label="Get a different name"
                  onClick={onShuffle}
                >
                  <ShuffleIcon />
                </button>
              )}
            </div>
          </label>
          <button type="button" className={`primary ${styles.continueButton}`} onClick={onContinue}>
            Continue
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </div>
  );
}
