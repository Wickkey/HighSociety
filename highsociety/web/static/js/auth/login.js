// Login screen flow: guest continuation and Google sign-in.
import { $, hide, show, showError, showScreen } from '../utils/dom.js';
import {
  fetchJSON, currentRoomCode, refreshStatus, startPolling, showHomeTiles, showHomeTile, startRoomsPolling,
} from '../lobby/lobby.js';
import { saveProfile, renderProfileChip } from './profile.js';
import { prefetchAccountStats, showAccountScreen, showAchievementsScreen } from '../account/account.js';
import { showLeaderboardScreen } from '../lobby/leaderboard.js';
import { showGameHistoryScreen } from '../lobby/gameHistory.js';
import { showPlayerProfileScreen } from '../lobby/playerProfile.js';
import { onPlayClick } from '../lobby/matchmaking.js';

// Maps a direct/refreshed visit to one of the 8 static screen URLs
// (web_server.py's routes) back to the in-app navigation that would have
// produced it -- boot's own version of what each sidebar/tile button
// already does, so a bookmark or shared link lands on the right screen
// instead of always defaulting to the tile picker.
const BOOT_PATH_HANDLERS = {
  '/play': onPlayClick,
  '/join': () => { showScreen('screen-host-setup'); showHomeTile('join'); startRoomsPolling(); },
  '/host': () => { showScreen('screen-host-setup'); showHomeTile('host'); },
  '/rules': () => { showScreen('screen-host-setup'); showHomeTile('rules'); },
  '/leaderboard': showLeaderboardScreen,
  '/account': showAccountScreen,
  '/achievements': showAchievementsScreen,
  '/my-games': showGameHistoryScreen,
};

// Set while the login screen's username form is collecting a name for a
// first-time Google sign-in (see handleGoogleCredential) rather than a
// plain Guest continuation -- onLoginUsernameContinue branches on this to
// decide which endpoint (if any) the submitted username actually goes to.
let _pendingGoogleIdToken = null;

// Decodes a JWT's payload (base64url) WITHOUT verifying its signature --
// only ever used to read "sub" (Google's own stable per-user id) for this
// browser's own local bookkeeping (see saveProfile's google_id), *after*
// the server has already verified the very same token via
// /api/auth/google or /api/auth/google/claim_username. Never used for
// anything security-sensitive -- the server's own verification is what
// actually matters; this is just so the client doesn't have to ask the
// server to repeat back a claim already sitting in the token it already
// has.
function decodeJwtPayload(token) {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(base64));
  } catch (e) {
    return {};
  }
}

// The boot handler's own pre-login continuation (join a room already in
// the URL, or show the home tiles) -- now reached only once #screen-login
// resolves to a profile, via either path below, instead of running
// unconditionally at boot.
export function proceedPastLogin() {
  renderProfileChip(); // the header chip was last rendered at boot, before
                        // this browser had a profile at all -- refresh it
                        // now that saveProfile has actually run.
  prefetchAccountStats(); // see account.js -- so Account's own stats are already in hand by the time it's opened
  if (currentRoomCode()) {
    refreshStatus();
    startPolling();
    return;
  }
  // /account/<username> is a variant of /account (see web_server.py's own
  // route comment) -- always the current browser's own profile, never a
  // per-user viewer -- so it needs a prefix check rather than one exact-
  // match BOOT_PATH_HANDLERS entry per possible username.
  if (location.pathname.startsWith('/account/')) {
    showAccountScreen();
    return;
  }
  // /players/<username> carries a real per-user segment (unlike every
  // other entry in BOOT_PATH_HANDLERS below, which are all exact-match
  // static paths) -- a direct visit/refresh/shared link at this URL
  // opens that exact profile, same as clicking their name anywhere else.
  if (location.pathname.startsWith('/players/')) {
    const username = decodeURIComponent(location.pathname.slice('/players/'.length));
    showPlayerProfileScreen(username, 'leaderboard');
    return;
  }
  const bootHandler = BOOT_PATH_HANDLERS[location.pathname];
  if (bootHandler) {
    bootHandler();
    return;
  }
  showScreen('screen-host-setup');
  showHomeTiles();
  startRoomsPolling();
}

// showShuffle: only the guest path offers a "get a different name" reroll
// -- a first-time Google sign-in is picking their own real username, not
// an anonymous quirky one, so that button stays hidden there (see
// handleGoogleCredential).
function showLoginUsernameForm(prefillUsername, showShuffle) {
  hide($('login-username-error'));
  $('login-username').value = prefillUsername || '';
  $('btn-shuffle-username').classList.toggle('hidden', !showShuffle);
  show($('login-username-form'));
  $('login-username').focus();
}

async function fetchSuggestedGuestUsername() {
  try {
    const result = await fetchJSON('/api/auth/guest/suggest');
    return result.username || '';
  } catch (e) {
    return ''; // still a normal free-text field -- they can just type one
  }
}

// "Continue as Guest": a random, DB-reserved username (color + anime
// character + number) instead of asking someone to come up with one --
// see /api/auth/guest/suggest. Still a normal free-text field underneath
// (shuffle for a different one, or just type over it entirely).
export async function onContinueAsGuest() {
  _pendingGoogleIdToken = null;
  showLoginUsernameForm('', true);
  $('login-username').value = await fetchSuggestedGuestUsername();
}

export async function onShuffleUsername() {
  $('login-username').value = await fetchSuggestedGuestUsername();
  $('login-username').focus();
}

export async function onLoginUsernameContinue() {
  const username = $('login-username').value.trim();
  hide($('login-username-error'));
  if (!username) { showError($('login-username-error'), 'Username is required.'); return; }

  if (_pendingGoogleIdToken) {
    // Second step of a first-time Google sign-in: claim this username
    // for the account handleGoogleCredential already verified server-side
    // a moment ago.
    const token = _pendingGoogleIdToken;
    try {
      const result = await fetchJSON('/api/auth/google/claim_username', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: token, username }),
      });
      saveProfile(result.username, result.username, decodeJwtPayload(token).sub);
      _pendingGoogleIdToken = null;
      proceedPastLogin();
    } catch (e) {
      showError($('login-username-error'), e.message);
    }
    return;
  }

  // Guest path -- reserves the username in the same players table Google
  // accounts use (see /api/auth/guest/claim), so "unique username" is a
  // real, database-enforced guarantee rather than just a local label.
  try {
    const result = await fetchJSON('/api/auth/guest/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    });
    saveProfile(result.username, result.username, null);
    proceedPastLogin();
  } catch (e) {
    showError($('login-username-error'), e.message);
  }
}

// Google Identity Services calls this once the visitor actually picks an
// account in its own popup/One Tap UI (wired up by initGoogleSignIn below)
// -- response.credential is a signed ID token this browser never needs to
// (and can't meaningfully) verify itself; /api/auth/google does that
// server-side before this client trusts anything in it.
async function handleGoogleCredential(response) {
  hide($('login-username-error'));
  try {
    const result = await fetchJSON('/api/auth/google', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: response.credential }),
    });
    if (result.needs_username) {
      _pendingGoogleIdToken = response.credential;
      showLoginUsernameForm(result.suggested_display_name, false);
    } else {
      saveProfile(result.username, result.username, decodeJwtPayload(response.credential).sub);
      proceedPastLogin();
    }
  } catch (e) {
    _pendingGoogleIdToken = null;
    showLoginUsernameForm('', false);
    showError($('login-username-error'), e.message);
  }
}

// Invoked by index.html's own GIS <script> tag via its onload attribute --
// runs the instant the library is actually ready rather than guessing at
// a fixed delay, and only exists on the page at all when GOOGLE_CLIENT_ID
// was configured server-side (see web_server.py); window.GOOGLE_CLIENT_ID
// is simply never set otherwise, so this quietly does nothing everywhere
// else (every local dev run and the whole test suite included).
export function initGoogleSignIn() {
  if (!window.GOOGLE_CLIENT_ID || !window.google || !window.google.accounts) return;
  google.accounts.id.initialize({ client_id: window.GOOGLE_CLIENT_ID, callback: handleGoogleCredential });
  google.accounts.id.renderButton($('google-signin-container'), {
    theme: 'filled_black', size: 'large', width: 280, text: 'continue_with',
  });
}
// index.html's inline onload="window.initGoogleSignIn && initGoogleSignIn()"
// calls this by name on `window` -- ES module top-level functions are not
// implicitly global, so this has to be attached explicitly (see the
// module-split plan's own note on this being the one inline handler in
// index.html that calls a named app function rather than doing raw DOM
// manipulation inline).
window.initGoogleSignIn = initGoogleSignIn;
