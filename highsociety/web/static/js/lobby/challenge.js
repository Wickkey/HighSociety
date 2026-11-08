// Challenge links: /challenge/<bot>, "Can you defeat Thalapathy Bot?", one per
// Insane bot. Anyone can open one, signed in or not (search engines and link
// previews too); accepting starts a game against that bot and another Insane
// one (see web_server.py's challenge_page and api_create_game). The list comes
// with the page (window.CHALLENGES), so nothing here waits on a fetch to lay out.
import { $, hide, show, showError, showScreen, setScreenPath } from '../utils/dom.js';
import { setAvatar } from '../ui/avatar.js';
import { showToast } from '../ui/notifications.js';
import { fetchJSON, startChallengeGame } from './lobby.js';
import { loadProfile } from '../auth/profile.js';

// Set when a visitor accepts before signing in: the game starts once they have.
const ACCEPT_AFTER_SIGN_IN = 'hs_challenge_accept';

let shownKey = null;

export function challenges() {
  return window.CHALLENGES || [];
}

export function findChallenge(key) {
  return challenges().find((c) => c.key === key) || null;
}

// By a profile username ("__bot_thanos__") or a seat name ("Thanos Bot").
export function challengeForProfile(username) {
  return challenges().find((c) => c.username === username) || null;
}

export function challengeForSeatName(name) {
  const lower = (name || '').toLowerCase();
  return challenges().find((c) => c.name.toLowerCase() === lower) || null;
}

export function challengeKeyFromPath(path = location.pathname) {
  const match = path.match(/^\/challenge\/([a-z0-9_]+)\/?$/);
  return match && findChallenge(match[1]) ? match[1] : null;
}

function challengeUrl(key) {
  return `${location.origin}/challenge/${key}`;
}

function sessionGet(name) {
  try { return sessionStorage.getItem(name); } catch (e) { return null; }
}

function sessionSet(name, value) {
  try {
    if (value == null) sessionStorage.removeItem(name);
    else sessionStorage.setItem(name, value);
  } catch (e) { /* private mode: the visitor just presses Accept again */ }
}

// The phone's share sheet where there is one, otherwise the link is copied
// (a desktop share sheet is a detour when you just want to paste it somewhere).
async function shareLink(title, text, url) {
  if (navigator.share && window.matchMedia('(pointer: coarse)').matches) {
    try {
      await navigator.share({ title, text, url });
      return;
    } catch (e) {
      if (e && e.name === 'AbortError') return; // they closed the sheet
    }
  }
  try {
    await navigator.clipboard.writeText(`${text} ${url}`);
    showToast('Link copied. Paste it anywhere to share.');
  } catch (e) {
    window.prompt('Copy this link to share it:', url);
  }
}

// outcome: 'won' (you beat it), 'lost' (it beat you), or null (just the challenge).
export function shareChallenge(challenge, outcome = null) {
  const text = outcome === 'won' ? `I beat ${challenge.name} at High Society. Can you?`
    : outcome === 'lost' ? `${challenge.name} beat me at High Society. Can you beat it?`
      : `Can you defeat ${challenge.name}? Play High Society free in your browser.`;
  return shareLink(`Can you defeat ${challenge.name}?`, text, challengeUrl(challenge.key));
}

function renderStats(challenge) {
  const slot = $('challenge-stats');
  slot.textContent = '';
  slot.classList.add('tile-loading-spinner');
  fetchJSON(`/api/profile/${encodeURIComponent(challenge.username)}`).then((stats) => {
    if (shownKey !== challenge.key) return;
    slot.classList.remove('tile-loading-spinner');
    const games = stats.games_played;
    slot.textContent = games
      ? `Elo ${stats.elo} · won ${Math.round(stats.win_rate * 100)}% of ${games} ${games === 1 ? 'game' : 'games'}`
      : 'No games yet. Be the first to try.';
  }).catch(() => {
    if (shownKey !== challenge.key) return;
    slot.classList.remove('tile-loading-spinner');
    slot.textContent = 'An Insane bot: the strongest there is.';
  });
}

function renderOthers(current) {
  const list = $('challenge-others');
  list.innerHTML = '';
  challenges().filter((c) => c.key !== current.key).forEach((c) => {
    const link = document.createElement('a');
    link.className = 'challenge-other';
    link.href = `/challenge/${c.key}`;
    link.dataset.challengeKey = c.key;
    const avatar = document.createElement('span');
    avatar.className = 'profile-chip-avatar challenge-other-avatar';
    setAvatar(avatar, c.name, c.avatar_url);
    const name = document.createElement('span');
    name.textContent = c.name;
    link.append(avatar, name);
    list.appendChild(link);
  });
}

export function showChallengeScreen(key) {
  const challenge = findChallenge(key);
  if (!challenge) return false;
  shownKey = key;
  showScreen('screen-challenge');
  setScreenPath(`/challenge/${key}`);
  document.title = `Can you defeat ${challenge.name}? – High Society`;
  setAvatar($('challenge-avatar'), challenge.name, challenge.avatar_url);
  $('challenge-name').textContent = `${challenge.name}?`;
  $('challenge-bio').textContent = challenge.bio;
  hide($('challenge-error'));
  const accept = $('btn-challenge-accept');
  accept.disabled = false;
  accept.textContent = 'Accept challenge';
  renderStats(challenge);
  renderOthers(challenge);
  if (loadProfile() && sessionGet(ACCEPT_AFTER_SIGN_IN) === key) {
    sessionSet(ACCEPT_AFTER_SIGN_IN, null);
    onAcceptChallenge();
  }
  return true;
}

export async function onAcceptChallenge() {
  const key = shownKey;
  if (!key) return;
  if (!loadProfile()) {
    // Signing in comes first; showChallengeScreen picks this up afterwards.
    sessionSet(ACCEPT_AFTER_SIGN_IN, key);
    document.body.classList.remove('visitor-browsing');
    showScreen('screen-login');
    return;
  }
  const accept = $('btn-challenge-accept');
  accept.disabled = true;
  accept.textContent = 'Starting…';
  hide($('challenge-error'));
  try {
    await startChallengeGame(key);
  } catch (e) {
    showError($('challenge-error'), e.message || 'Couldn\'t start the game. Try again in a moment.');
    accept.disabled = false;
    accept.textContent = 'Accept challenge';
  }
}

export function onShareShownChallenge() {
  const challenge = findChallenge(shownKey);
  if (challenge) shareChallenge(challenge);
}
