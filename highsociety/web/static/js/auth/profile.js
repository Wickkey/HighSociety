// Persistent device identity (localStorage + cookie fallback), the header
// profile chip, its popover, and logout.
import { $, hide, show, showScreen } from '../utils/dom.js';
import { PROFILE_STORAGE_KEY } from '../utils/constants.js';
// Circular with lobby.js (lobby.js needs loadProfile) -- safe, both sides
// only touch these inside function bodies, never at module-evaluation time.
import { applyJoinIdentityDefaults, clearCurrentRoomCode } from '../lobby/lobby.js';

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
export function saveProfile(username, name, googleId) {
  const existing = loadProfile();
  const resolvedGoogleId = googleId !== undefined ? googleId : (existing ? existing.google_id : null) || null;
  const value = JSON.stringify({ username, name, google_id: resolvedGoogleId });
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
export function onLogout() {
  try { localStorage.removeItem(PROFILE_STORAGE_KEY); } catch (e) { /* private mode, etc. */ }
  writeCookie(PROFILE_STORAGE_KEY, '', -1);
  closeProfilePopover();
  renderProfileChip();
  clearCurrentRoomCode();
  history.replaceState(null, '', location.pathname);
  showScreen('screen-login');
}
