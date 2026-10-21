// Persistent device identity (localStorage + cookie fallback), the header
// profile chip, its popover, and logout.
import { $, hide, show, showScreen } from '../utils/dom.js';
import { PROFILE_STORAGE_KEY } from '../utils/constants.js';
// Circular with lobby.js (lobby.js needs loadProfile) -- safe, both sides
// only touch these inside function bodies, never at module-evaluation time.
import { applyJoinIdentityDefaults, clearCurrentRoomCode } from '../lobby/lobby.js';
import { confirmDialog } from '../ui/modals.js';

function readCookie(key) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = document.cookie.match(new RegExp(`(?:^|; )${escaped}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function writeCookie(key, value, days) {
  try {
    const expires = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toUTCString();
    document.cookie = `${key}=${encodeURIComponent(value)}; expires=${expires}; path=/; samesite=lax`;
  } catch (e) {
    // Cookies disabled/blocked — localStorage (the primary copy) still works.
  }
}

export function loadProfile() {
  let raw = null;
  try { raw = localStorage.getItem(PROFILE_STORAGE_KEY); } catch (e) { /* private mode, etc. */ }
  if (!raw) raw = readCookie(PROFILE_STORAGE_KEY);
  if (!raw) return null;
  try {
    const profile = JSON.parse(raw);
    return (profile && profile.username && profile.name) ? profile : null;
  } catch (e) {
    return null;
  }
}

// googleId: omit entirely to leave whatever was already stored alone
// (e.g. onAccountSaveClick editing just the display name shouldn't wipe
// out "this profile came from a real Google account"); pass null
// explicitly for a guest profile with no account behind it at all.
// loginToken: same omit-to-keep rule -- the server-issued proof that this
// browser really is `username` (see web_server.py's _identity_rejected);
// a rename keeps it, since it's tied to the account, not the name.
export function saveProfile(username, name, googleId, loginToken) {
  const existing = loadProfile();
  const resolvedGoogleId = googleId !== undefined ? googleId : (existing ? existing.google_id : null) || null;
  const resolvedToken = loginToken !== undefined ? loginToken : (existing ? existing.login_token : null) || null;
  const value = JSON.stringify({ username, name, google_id: resolvedGoogleId, login_token: resolvedToken });
  try { localStorage.setItem(PROFILE_STORAGE_KEY, value); } catch (e) { /* fall through to the cookie */ }
  writeCookie(PROFILE_STORAGE_KEY, value, 365);
}

// "What you're currently doing" -- deliberately separate from the
// identity chip next to it (that one only ever answers "who you are",
// see renderProfileChip). Each status gets its own small icon rather than
// reusing generic prose, matching the app's existing badge language
// (compare .home-tile-live-badge) instead of a plain text pill.
const SESSION_STATUS = {
  connecting: { label: 'Connecting…', icon: '' },
  reconnecting: { label: 'Reconnecting…', icon: '' },
  // Seated, but the table isn't full yet -- "Playing" here read as if the
  // game had started.
  waiting: {
    label: 'Waiting for players',
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  },
  finished: {
    label: 'Game over',
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/></svg>',
  },
  playing: {
    label: 'Playing',
    icon: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 4.5v15l14-7.5z"/></svg>',
  },
  spectating: {
    label: 'Spectating',
    icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>',
  },
};

export function setSessionStatus(status) {
  const config = SESSION_STATUS[status];
  const badge = $('session-status-badge');
  badge.className = `session-status-badge ${status}`;
  badge.innerHTML = `${config.icon}<span>${config.label}</span>`;
  // A live room session's identity is already fixed — editing the *saved*
  // profile wouldn't change the current seat, so the chip stops being an
  // "edit" target for as long as any status here is showing (see
  // renderProfileChip, which re-enables it, and hides this badge, once
  // back at the idle state).
  $('connection-badge').classList.remove('editable', 'needs-attention');
  closeProfilePopover();
}

// The top-right chip is the one persistent "profile area" (see
// #profile-chip-wrap in index.html): whenever this browser isn't currently
// locked into a room session, it shows the saved name (or "Guest" — a
// placeholder that itself signals "click me to set this") and opens the
// popover below on click. setSessionStatus() above is what locks it (and
// shows the separate status badge) during an actual session, where the
// joined identity is already fixed and editing the *saved* profile
// wouldn't change anything about the current seat anyway -- calling this
// is also what un-locks it and hides that badge again, once idle.
export function renderProfileChip() {
  const badge = $('connection-badge');
  const profile = loadProfile();
  $('connection-badge-avatar').textContent = profile ? profile.username.charAt(0).toUpperCase() : '?';
  $('connection-badge-text').textContent = profile ? profile.name : 'Username';
  badge.classList.remove('hidden');
  badge.classList.add('editable');
  // Glows until a real profile is saved (see ensureProfileSet/
  // account.js's onAccountSaveClick) -- a passive "this still needs you"
  // cue, gone for good the moment one exists.
  badge.classList.toggle('needs-attention', !profile);
  $('session-status-badge').classList.add('hidden');
  closeProfilePopover();
  applyJoinIdentityDefaults();
}

// A short menu (Account / Log out) now, not inline edit fields -- editing
// itself lives on the Account screen (see account/account.js).
export function openProfilePopover() {
  const profile = loadProfile();
  // Nothing to log out of until a profile actually exists.
  $('btn-logout').classList.toggle('hidden', !profile);
  $('popover-menu-divider').classList.toggle('hidden', !profile);
  show($('profile-popover'));
}

export function closeProfilePopover() {
  hide($('profile-popover'));
}

// Guards the "Host Game" / "Join" actions on the home screen: if this
// browser has somehow reached them with no saved profile (should be rare
// now that login is mandatory before the home screen is ever reachable --
// this is a defensive fallback, not the normal path), send it back to
// login rather than the home action proceeding under no identity at all.
// Purely a client-side check against the already-cached profile (see
// loadProfile) -- no network round-trip, so it adds no latency/backend load
// to the host/join request it's guarding. Returns true if the action should
// be aborted so callers can `if (ensureProfileSet()) return;`.
export function ensureProfileSet() {
  if (loadProfile()) return false;
  showScreen('screen-login');
  return true;
}

export function onProfileChipClick() {
  if (!$('connection-badge').classList.contains('editable')) return; // locked into a room session
  if ($('profile-popover').classList.contains('hidden')) {
    openProfilePopover();
  } else {
    closeProfilePopover();
  }
}

// Clears this browser's saved identity entirely (both localStorage and
// the cookie fallback -- see saveProfile/loadProfile) and returns to the
// login screen so a different account/guest name can be chosen. Doesn't
// touch anything server-side -- a Google account itself isn't "logged
// out" of, only this browser's own cached association with it, exactly
// mirroring how loadProfile/saveProfile never talked to the server for
// a guest profile either.
// Sent with every request that acts as this player (taking a seat,
// hosting, renaming) -- null for a profile from before tokens existed,
// until upgradeLegacyProfile below swaps one in.
export function loginToken() {
  const profile = loadProfile();
  return (profile && profile.login_token) || null;
}

// A profile saved before login tokens existed has none. A guest gets one
// on the spot; a Google-linked account (or a guest name some other device
// already holds) has to sign in again -- resolves false in that case,
// after clearing the stale profile. Network trouble resolves true and
// leaves things as they are: the server lets a request without a valid
// token through only when it can't check, so nothing is lost by waiting.
export async function upgradeLegacyProfile() {
  const profile = loadProfile();
  if (!profile || profile.login_token) return true;
  let res;
  try {
    res = await fetch('/api/auth/upgrade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: profile.username }),
    });
  } catch (e) {
    return true;
  }
  if (res.status === 403) {
    clearSavedProfile();
    return false;
  }
  if (!res.ok) return true;
  const body = await res.json().catch(() => ({}));
  if (body.login_token) saveProfile(profile.username, profile.name, undefined, body.login_token);
  return true;
}

function clearSavedProfile() {
  try { localStorage.removeItem(PROFILE_STORAGE_KEY); } catch (e) { /* private mode, etc. */ }
  writeCookie(PROFILE_STORAGE_KEY, '', -1);
}

export async function onLogout() {
  const profile = loadProfile();
  if (profile && !profile.google_id) {
    // A guest's name is reserved for them, but logging out throws away the
    // only proof it's theirs (the login token) -- there's no way back to
    // it or its game history afterwards, so say so first.
    closeProfilePopover();
    const ok = await confirmDialog(
      `You're playing as a guest. If you log out, you can't get back to "${profile.username}" or its game history. `
      + 'Sign in with Google next time to keep your account across devices.',
      'Log out',
    );
    if (!ok) return;
  }
  clearSavedProfile();
  closeProfilePopover();
  renderProfileChip();
  clearCurrentRoomCode();
  history.replaceState(null, '', location.pathname);
  showScreen('screen-login');
}
