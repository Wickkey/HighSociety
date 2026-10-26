// Login screen flow: guest continuation and Google sign-in.
import { $, hide, show, showError, showScreen } from '../utils/dom.js';
import {
  fetchJSON, currentRoomCode, refreshStatus, startPolling, showHomeTiles, showHomeTile, startRoomsPolling,
} from '../lobby/lobby.js';
import { loadProfile, loginToken, saveProfile, renderProfileChip } from './profile.js';
import { googleSignInConfigured, renderGoogleButtons } from '../ui/googleButton.js';
import { attachUsernameField } from '../ui/usernameField.js';
import { showToast } from '../ui/notifications.js';
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
  showChooseStep(); // so a later log-out lands on the first step again
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

const GOOGLE_PERK = 'Keeps your rating, games and achievements on every device.';

let _usernameField = null;
function usernameField() {
  if (!_usernameField) {
    _usernameField = attachUsernameField({
      field: $('login-username-field'),
      input: $('login-username'),
      status: $('login-username-status'),
      count: $('login-username-count'),
      submit: $('btn-login-continue'),
    });
  }
  return _usernameField;
}

// The perk line under the Google button doubles as its status: "Signing
// you in" while the server checks the account, or why it failed.
function setGoogleStatus(text, kind) {
  const el = $('login-google-status');
  el.textContent = text;
  el.classList.toggle('is-busy', kind === 'busy');
  el.classList.toggle('is-error', kind === 'error');
}

// Step 1: Google or guest. Also where the screen resets to, so it never
// reopens mid-way through an earlier sign-in.
export function showChooseStep() {
  _pendingGoogleIdToken = null;
  $('login-google-block').classList.toggle('hidden', !googleSignInConfigured());
  setGoogleStatus(GOOGLE_PERK);
  $('btn-continue-guest').disabled = false;
  hide($('login-step-name'));
  show($('login-step-choose'));
}

// Step 2: pick a username. A guest gets a random free name with a shuffle
// button; a first-time Google account gets its own name, or the first free
// variant of it ("Vignesh 2"), already checked as available.
function showNameStep({ google, email, prefill }) {
  hide($('login-step-choose'));
  hide($('login-username-error'));
  $('login-google-identity').classList.toggle('hidden', !google);
  $('login-google-email').textContent = email || 'Google account';
  $('login-name-title').textContent = google ? 'Choose your username' : 'Pick a guest name';
  $('login-name-sub').textContent = google
    ? 'This is how other players see you. You can change it later from your Account page.'
    : 'We picked a random one. Shuffle for another, or type your own.';
  $('btn-shuffle-username').classList.toggle('hidden', google);
  const field = usernameField();
  field.setBusy(false, 'Continue');
  show($('login-step-name'));
  if (prefill === undefined) {
    // Guest: the random name is still on its way -- hold the spinner in place.
    $('login-username').value = '';
    $('login-username-field').dataset.state = 'checking';
    $('login-username-status').className = 'username-status username-status-checking';
    $('login-username-status').textContent = 'Finding you a name…';
    $('login-username-count').textContent = '';
    $('btn-login-continue').disabled = true;
  } else {
    field.setKnownAvailable(prefill);
  }
  $('login-username').focus();
}

export function onLoginBack() {
  showChooseStep();
  $('btn-continue-guest').focus();
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
  showNameStep({ google: false });
  const name = await fetchSuggestedGuestUsername();
  // Typed over it already? Keep what they typed.
  if (!$('login-username').value) usernameField().setKnownAvailable(name);
}

export async function onShuffleUsername() {
  const btn = $('btn-shuffle-username');
  btn.classList.add('is-spinning');
  const name = await fetchSuggestedGuestUsername();
  btn.classList.remove('is-spinning');
  usernameField().setKnownAvailable(name);
  $('login-username').focus();
}

// Both paths end here: the Google one claims the name for the account
// handleGoogleCredential already verified; the guest one reserves it in the
// same players table Google accounts use (see /api/auth/guest/claim), so
// "unique username" is a real, database-enforced guarantee.
export async function onLoginUsernameContinue() {
  const field = usernameField();
  const btn = $('btn-login-continue');
  if (btn.classList.contains('is-busy')) return;
  hide($('login-username-error'));
  if (!(await field.ready())) return;
  const username = $('login-username').value.trim();
  const token = _pendingGoogleIdToken;
  field.setBusy(true, token ? 'Creating your account…' : 'Joining…');
  try {
    const result = await fetchJSON(token ? '/api/auth/google/claim_username' : '/api/auth/guest/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(token ? { id_token: token, username } : { username }),
    });
    saveProfile(result.username, result.username, token ? decodeJwtPayload(token).sub : null, result.login_token || null);
    proceedPastLogin();
  } catch (e) {
    if (e.status === 409) field.showTaken(e.body && e.body.suggestion);
    else showError($('login-username-error'), e.message);
  } finally {
    field.setBusy(false, 'Continue');
  }
}

// Google Identity Services calls this once the visitor actually picks an
// account in its own popup/One Tap UI (wired up by initGoogleSignIn below)
// -- response.credential is a signed ID token this browser never needs to
// (and can't meaningfully) verify itself; /api/auth/google does that
// server-side before this client trusts anything in it.
async function handleGoogleCredential(response) {
  const current = loadProfile();
  if (current && !current.google_id) {
    connectGoogleToGuest(response.credential, current);
    return;
  }
  setGoogleStatus('Signing you in…', 'busy');
  $('btn-continue-guest').disabled = true;
  try {
    const result = await fetchJSON('/api/auth/google', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: response.credential }),
    });
    if (result.needs_username) {
      _pendingGoogleIdToken = response.credential;
      showNameStep({
        google: true,
        email: decodeJwtPayload(response.credential).email,
        prefill: result.suggested_username || result.suggested_display_name || '',
      });
    } else {
      saveProfile(result.username, result.username, decodeJwtPayload(response.credential).sub, result.login_token || null);
      proceedPastLogin();
    }
  } catch (e) {
    showChooseStep();
    setGoogleStatus(e.message, 'error');
  }
}

// "Connect Google" from a guest account (Account or Achievements screen):
// the guest keeps its username and every past game, and the achievements
// those games earned unlock now (see /api/auth/google/link). If this Google
// account already had a profile, the guest's games move into it and this
// browser switches to that profile instead.
async function connectGoogleToGuest(credential, profile) {
  try {
    const result = await fetchJSON('/api/auth/google/link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: credential, username: profile.username, login_token: loginToken() }),
    });
    saveProfile(result.username, result.username, decodeJwtPayload(credential).sub, result.login_token || null);
    renderProfileChip();
    const n = result.achievements_unlocked || 0;
    const unlocked = n ? ` ${n} achievement${n === 1 ? '' : 's'} unlocked from your past games.` : '';
    showToast(result.merged
      ? `Google connected. Your guest games are now part of ${result.username}.${unlocked}`
      : `Google connected.${unlocked}`, 5000);
    if (!$('screen-achievements').classList.contains('hidden')) showAchievementsScreen();
    else if (!$('screen-account').classList.contains('hidden')) showAccountScreen();
  } catch (e) {
    showToast(e.message, 5000);
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
  renderGoogleButtons();
}
// index.html's inline onload="window.initGoogleSignIn && initGoogleSignIn()"
// calls this by name on `window` -- ES module top-level functions are not
// implicitly global, so this has to be attached explicitly (see the
// module-split plan's own note on this being the one inline handler in
// index.html that calls a named app function rather than doing raw DOM
// manipulation inline).
window.initGoogleSignIn = initGoogleSignIn;
