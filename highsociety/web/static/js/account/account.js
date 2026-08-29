// Account screen: profile editing, achievements grid, public stats.
import { $, hide, show, showError, showScreen, setScreenPath } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { loadProfile, saveProfile, renderProfileChip } from '../auth/profile.js';
import { fetchJSON } from '../lobby/lobby.js';
import { showToast } from '../ui/notifications.js';

// Static catalog mirroring highsociety/code/common/achievements.py's
// ACHIEVEMENTS -- small enough to duplicate client-side (12 entries) rather
// than round-trip it over the network; ids must stay in sync with the
// backend's, since those ids are what /api/achievements returns as unlocked.
// Icons are 24x24 stroke line-art, same design language as the sidebar/
// home-tile icons (fill:none, stroke:currentColor, ~1.6 width, round caps).
const ACHIEVEMENTS = [
  { id: 'first_win', name: 'First Victory', description: 'Win a game.',
    icon: '<path d="M12 3.4l2.1 4.4 4.8.6-3.5 3.4.9 4.8L12 14.3l-4.3 2.3.9-4.8-3.5-3.4 4.8-.6L12 3.4z"/>' },
  { id: 'hat_trick', name: 'Hat Trick', description: 'Win 3 games.',
    icon: '<path d="M6 14.8l.85 1.9 2 .3-1.45 1.4.35 2-1.75-.95-1.75.95.35-2-1.45-1.4 2-.3z"/>'
        + '<path d="M12 4l1.3 2.9 3.1.4-2.3 2.2.6 3.1L12 11l-2.7 1.6.6-3.1-2.3-2.2 3.1-.4z"/>'
        + '<path d="M18 14.8l.85 1.9 2 .3-1.45 1.4.35 2-1.75-.95-1.75.95.35-2-1.45-1.4 2-.3z"/>' },
  { id: 'high_society_regular', name: 'High Society Regular', description: 'Win 5 games.',
    icon: '<circle cx="12" cy="8" r="4.3"/><path d="M9 11.6L6 21l3.4-1.8 2.6 1.8"/><path d="M15 11.6L18 21l-3.4-1.8-2.6 1.8"/>' },
  { id: 'old_money', name: 'Old Money', description: 'Win 10 games.',
    icon: '<path d="M4 19h16"/><path d="M5 19l-1-8 4 3 4-6 4 6 4-3-1 8"/>'
        + '<circle cx="12" cy="6.3" r="1" fill="currentColor" stroke="none"/>'
        + '<circle cx="6" cy="9.8" r="0.85" fill="currentColor" stroke="none"/>'
        + '<circle cx="18" cy="9.8" r="0.85" fill="currentColor" stroke="none"/>' },
  { id: 'giant_slayer', name: 'Giant Slayer', description: 'Win a game with a Hard bot at the table.',
    icon: '<path d="M12 2v12.3"/><path d="M9 14.3h6"/><path d="M12 15v2.3"/><circle cx="12" cy="18.6" r="1.1" fill="currentColor" stroke="none"/>' },
  { id: 'sniper', name: 'Sniper', description: 'Win an auction that only ever had one bid -- yours.',
    icon: '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/>'
        + '<path d="M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21"/>' },
  { id: 'free_lunch', name: 'Free Lunch', description: 'Win an auction paying nothing.',
    icon: '<circle cx="12" cy="12" r="8"/><path d="M5 19L19 5"/>' },
  { id: 'minimalist', name: 'Minimalist', description: 'Win an auction for the lowest-value Painting.',
    icon: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/>' },
  { id: 'full_set', name: 'Full Set', description: 'Collect all 3 Prestige cards in one game.',
    icon: '<path d="M5 9l3 3-3 3-3-3z"/><path d="M12 9l3 3-3 3-3-3z"/><path d="M19 9l3 3-3 3-3-3z"/>' },
  { id: 'collector', name: 'Collector', description: 'Win at least one of every card type offered in a game.',
    icon: '<rect x="4" y="7" width="9" height="13" rx="1.3" transform="rotate(-18 8.5 13.5)"/>'
        + '<rect x="7.5" y="6" width="9" height="13" rx="1.3"/>'
        + '<rect x="11" y="7" width="9" height="13" rx="1.3" transform="rotate(18 15.5 13.5)"/>' },
  { id: 'fearless', name: 'Fearless', description: 'Win a game without ever passing or folding.',
    icon: '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/>' },
  { id: 'master_of_disgrace', name: 'Master of Disgrace', description: 'Win a disgrace (Faux-Pas-triggered) auction.',
    icon: '<path d="M12 4c-4.4 0-7.5 2.8-7.5 6.5 0 5 3.3 8.8 7.5 9.5 4.2-.7 7.5-4.5 7.5-9.5C19.5 6.8 16.4 4 12 4z"/>'
        + '<circle cx="9" cy="10.5" r="1" fill="currentColor" stroke="none"/>'
        + '<circle cx="15" cy="10.5" r="1" fill="currentColor" stroke="none"/>'
        + '<path d="M9 16c1.5-1.3 4.5-1.3 6 0"/>' },
];

function renderAchievementTile(a, unlocked) {
  return `<div class="achievement-tile ${unlocked ? 'unlocked' : 'locked'}" title="${escapeHtml(a.description)}">`
    + `<span class="achievement-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" `
    + `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${a.icon}</svg></span>`
    + `<span class="achievement-name">${escapeHtml(a.name)}</span></div>`;
}

// Fired once as soon as boot knows who's signed in (see login.js's
// proceedPastLogin) rather than waiting for the user to actually click
// into Account -- the query itself is cheap, but it's still a real round
// trip to remote Supabase (2-3s isn't unusual, see this session's own
// production timing notes), so front-loading it while they're still
// browsing the home screen removes that wait from the moment they open
// Account. Keyed by username so a mid-session identity change (login/
// logout/username edit) doesn't serve a stale prefetch for someone else.
let _statsPrefetch = null;
let _statsPrefetchUsername = null;

export function prefetchAccountStats() {
  const profile = loadProfile();
  if (!profile) return;
  _statsPrefetchUsername = profile.username;
  _statsPrefetch = fetchJSON(`/api/profile/${encodeURIComponent(profile.username)}`).catch(() => null);
}

// account-stats-row.loading CSS) the instant the screen opens, then
// swapped for real numbers once /api/profile resolves -- previously the
// whole row was just `hidden` until the fetch finished, so the cards
// appeared out of nowhere rather than looking like they were loading.
// Stats aren't gated to Google-linked accounts (get_player_profile_stats
// isn't either) -- a guest with no games yet just keeps the skeleton
// (the 404 case below), same as before.
function renderAccountStats(stats, profile) {
  if (!stats) return;
  // A guest's elo column never actually moves (record_finished_game only
  // rates google_id-linked players and bots) -- showing the untouched
  // default as if it meant something would just be misleading.
  $('account-elo').textContent = profile.google_id ? stats.elo : 'Unrated';
  $('account-stat-games').textContent = stats.games_played;
  $('account-stat-wins').textContent = stats.wins;
  $('account-stat-winrate').textContent = `${Math.round(stats.win_rate * 100)}%`;
  // null for a player whose only games predate the game_results table
  // (see get_player_profile_stats' own docstring) -- an em dash reads
  // as "no data" rather than a misleading 0.
  $('account-stat-avg-placement').textContent = stats.avg_placement != null ? stats.avg_placement.toFixed(1) : '—';
  $('account-stat-avg-points').textContent = stats.avg_points != null ? stats.avg_points.toFixed(1) : '—';
  $('account-stat-avg-money').textContent = stats.avg_money_remaining != null ? stats.avg_money_remaining.toFixed(1) : '—';
  $('account-stats-row').classList.remove('loading');
}

// Stale-while-revalidate, deliberately: the login-time prefetch is only
// ever trusted for the *instant* first paint (removing the visible wait
// this screen used to have) -- every open of this screen still follows
// up with a genuinely fresh fetch and re-renders on top of it. A real,
// reported bug was the prefetch being treated as the last word: it's
// fetched exactly once per page load, so any game finished afterward
// (including a chain of rematches, which never revisits the home screen
// where an invalidation hook could otherwise live) left Account showing
// numbers from before that game -- a real, live-reported case: Elo and
// avg. money both stuck at whatever they were after just the *first* of
// two games played in one sitting, disagreeing with the Leaderboard
// (which always fetches fresh) and with the games actually played.
async function loadAccountStats() {
  const profile = loadProfile();
  const row = $('account-stats-row');
  row.classList.add('loading');
  if (!profile) return;
  if (_statsPrefetch && _statsPrefetchUsername === profile.username) {
    try {
      renderAccountStats(await _statsPrefetch, profile);
    } catch (e) { /* fall through to the fresh fetch below */ }
  }
  try {
    renderAccountStats(await fetchJSON(`/api/profile/${encodeURIComponent(profile.username)}`), profile);
  } catch (e) { /* 404: no games recorded yet -- leave whatever's already rendered (or the skeleton) */ }
}

// Guests have no stable identity to hang persistent progress off (a
// guest's username is regenerated every time their browser profile
// resets -- see saveProfile), so achievements only ever start counting
// once someone is actually signed in with Google (see
// game_history.record_finished_game's google_id gating). Every tile still
// renders, just permanently locked, plus a one-time sign-in nudge.
async function loadAchievements() {
  const profile = loadProfile();
  const grid = $('achievements-grid');
  const guestNote = $('account-achievements-guest-note');
  grid.innerHTML = ACHIEVEMENTS.map((a) => renderAchievementTile(a, false)).join('');
  if (!profile) return;
  if (!profile.google_id) {
    show(guestNote);
    showToast('Sign in with Google to start unlocking achievements.');
    return;
  }
  hide(guestNote);
  try {
    const result = await fetchJSON(`/api/achievements?username=${encodeURIComponent(profile.username)}`);
    const unlocked = new Set(result.achievements || []);
    grid.innerHTML = ACHIEVEMENTS.map((a) => renderAchievementTile(a, unlocked.has(a.id))).join('');
  } catch (e) { /* transient network error -- leave the grid locked rather than block the screen */ }
}

// Reached via the popover's "Account" item or the sidebar's Account tab --
// a full screen rather than the old inline popover fields, room to grow
// into "possibly do much more in future" (match history, stats, ...)
// without cramming it into a 260px dropdown.
export function showAccountScreen() {
  const profile = loadProfile();
  $('account-avatar').textContent = profile ? profile.username.charAt(0).toUpperCase() : '?';
  $('account-username-display').textContent = profile ? profile.username : '';
  $('account-username-input').value = profile ? profile.username : '';
  $('account-elo').textContent = profile && !profile.google_id ? 'Unrated' : '1000';
  // No prior indication anywhere in the app that a signed-in account is
  // actually Google-linked (chess.com's "Connected Accounts" panel was
  // the reference) -- same profile.google_id branch the Elo
  // Unrated/numeric split above already uses.
  $('account-google-badge').classList.toggle('hidden', !(profile && profile.google_id));
  $('account-guest-badge').classList.toggle('hidden', !(profile && !profile.google_id));
  if (profile && !profile.google_id) {
    showToast('Sign in with Google to start tracking your Elo rating.');
  }
  hide($('account-username-edit')); // collapsed by default -- see onAccountEditUsernameClick
  hide($('account-error'));
  hide($('account-saved'));
  showScreen('screen-account');
  setScreenPath('/account');
  loadAccountStats();
}

// The pencil next to the username -- reveals the same input+Save that
// used to just sit there permanently open. Re-syncs the input to the
// current saved value every time (not just on showAccountScreen) so
// re-opening after a Cancel never shows a stale in-progress edit.
export function onAccountEditUsernameClick() {
  const profile = loadProfile();
  $('account-username-input').value = profile ? profile.username : '';
  hide($('account-error'));
  hide($('account-saved'));
  show($('account-username-edit'));
  $('account-username-input').focus();
}

export function onAccountCancelEditClick() {
  hide($('account-username-edit'));
  hide($('account-error'));
}

// Its own sidebar tab (previously folded into Account) -- same catalog,
// same unlock logic, just reachable independently of profile editing.
export function showAchievementsScreen() {
  showScreen('screen-achievements');
  setScreenPath('/achievements');
  loadAchievements();
}

// Only round-trips to the server when the username actually changed --
// re-saving an unchanged one (e.g. just opening the screen and clicking
// Save) shouldn't cost a network call or risk a spurious 409.
export async function onAccountSaveClick() {
  hide($('account-error'));
  hide($('account-saved'));
  const username = $('account-username-input').value.trim();
  if (!username) { showError($('account-error'), 'Username is required.'); return; }
  const existing = loadProfile();
  if (existing && existing.username === username) {
    hide($('account-username-edit'));
    show($('account-saved'));
    return;
  }
  try {
    const result = await fetchJSON('/api/auth/username/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_username: existing ? existing.username : username, new_username: username }),
    });
    saveProfile(result.username, result.username);
    renderProfileChip();
    $('account-username-display').textContent = result.username;
    // Collapses back to plain text -- an error, by contrast, leaves this
    // open (see the catch below) so there's something left to fix.
    hide($('account-username-edit'));
    show($('account-saved'));
  } catch (e) {
    showError($('account-error'), e.message);
  }
}
