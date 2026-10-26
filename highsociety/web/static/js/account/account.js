// Account screen: profile editing, achievements grid, public stats, the
// Elo history chart, and the Recent Activity feed.
import { $, hide, show, showError, showScreen, setScreenPath } from '../utils/dom.js';
import { escapeHtml, timeAgo } from '../utils/formatting.js';
import { loadProfile, loginToken, saveProfile, renderProfileChip } from '../auth/profile.js';
import { fetchJSON } from '../lobby/lobby.js';
// Circular with gameHistory.js (which imports profileStats from
// here) -- safe by this project's own established convention: both sides
// only ever touch the other's export inside a function body, never at
// this module's own top-level evaluation. See gameHistory.js's identical
// note on its own import from here.
import { fetchGamesPage, renderIfChanged } from '../lobby/gameHistory.js';
import { createEloChartController, fetchRatingHistory } from '../ui/eloChart.js';
import { swr } from '../utils/swrCache.js';
import { googleSignInConfigured } from '../ui/googleButton.js';
import { attachUsernameField } from '../ui/usernameField.js';

// Static catalog mirroring highsociety/code/common/achievements.py's
// ACHIEVEMENTS -- small enough to duplicate client-side (36 entries) rather
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
  { id: 'dynasty', name: 'Dynasty', description: 'Win 25 games.',
    icon: '<path d="M4 21h16"/><path d="M6 21V5h2.5v2h2V5h3v2h2V5H18v16"/><path d="M10.5 21v-4a1.5 1.5 0 0 1 3 0v4"/>' },
  { id: 'on_a_roll', name: 'On a Roll', description: 'Win 3 games in a row.',
    icon: '<path d="M12 3c.5 3.5 5 5.5 5 10.5a5 5 0 0 1-10 0c0-2.4 1.3-3.8 2.5-5 .2 1.6 1 2.6 2 3 0-3 .3-5.8.5-8.5z"/>' },
  { id: 'socialite', name: 'Socialite', description: 'Play 10 games.',
    icon: '<path d="M8 3h8l-.6 5a3.4 3.4 0 0 1-6.8 0z"/><path d="M12 11.4V20"/><path d="M8.5 20.5h7"/>' },
  { id: 'veteran', name: 'Veteran', description: 'Play 50 games.',
    icon: '<path d="M8 3l2.5 6M16 3l-2.5 6"/><circle cx="12" cy="14.5" r="5.5"/><circle cx="12" cy="14.5" r="2.3"/>' },
  { id: 'rising_star', name: 'Rising Star', description: 'Reach a rating of 1100.',
    icon: '<path d="M10 6.5l1.5 3.1 3.4.5-2.5 2.4.6 3.4-3-1.6-3 1.6.6-3.4-2.5-2.4 3.4-.5z"/><path d="M18 21V9M15.5 11.5L18 9l2.5 2.5"/>' },
  { id: 'connoisseur', name: 'Connoisseur', description: 'Reach a rating of 1200.',
    icon: '<circle cx="10" cy="11" r="5.5"/><path d="M14 15l4 5.5"/><path d="M8 9.5a2.4 2.4 0 0 1 2-1"/>' },
  { id: 'aristocrat', name: 'Aristocrat', description: 'Reach a rating of 1300.',
    icon: '<path d="M8 15V5h8v10"/><path d="M4 18c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 2-8 2-8-.3-8-2z"/><path d="M8 12h8"/>' },
  { id: 'living_legend', name: 'Living Legend', description: 'Reach a rating of 1500.',
    icon: '<path d="M8 20c-3.5-2-5-6-4-11"/><path d="M16 20c3.5-2 5-6 4-11"/>'
        + '<path d="M5.2 13.5l-2.2-.5M6.6 16.6l-2 .3M4.6 10.2l-1.8-1M18.8 13.5l2.2-.5M17.4 16.6l2 .3M19.4 10.2l1.8-1"/>'
        + '<path d="M12 6l1 2.1 2.3.3-1.7 1.6.4 2.3-2-1.1-2 1.1.4-2.3-1.7-1.6 2.3-.3z"/>' },
  { id: 'giant_slayer', name: 'Giant Slayer', description: 'Win a game with a Hard bot at the table.',
    icon: '<path d="M12 2v12.3"/><path d="M9 14.3h6"/><path d="M12 15v2.3"/><circle cx="12" cy="18.6" r="1.1" fill="currentColor" stroke="none"/>' },
  { id: 'hard_times', name: 'Hard Times', description: 'Win a game with two or more Hard bots at the table.',
    icon: '<path d="M4 4l12 12M20 4L8 16"/><path d="M13.5 17.5l3-3M10.5 17.5l-3-3"/><path d="M16 18l2.5 2.5M8 18l-2.5 2.5"/>' },
  { id: 'good_company', name: 'Good Company', description: 'Win a game against another person.',
    icon: '<circle cx="9" cy="8" r="3"/><path d="M3.5 20c0-3.3 2.5-6 5.5-6s5.5 2.7 5.5 6"/>'
        + '<circle cx="16.5" cy="9" r="2.5"/><path d="M15.5 14.2c3 0 5 2.4 5 5.8"/>' },
  { id: 'landslide', name: 'Landslide', description: 'Win a game by 10 or more points.',
    icon: '<path d="M3 19l6-10 4 6 2.5-3.5L21 19z"/>' },
  { id: 'photo_finish', name: 'Photo Finish', description: 'Win a game by 1 point or less (a shared win counts).',
    icon: '<rect x="3" y="7" width="18" height="12" rx="2"/><path d="M8.5 7l1.5-2.5h4L15.5 7"/><circle cx="12" cy="13" r="3.3"/>' },
  { id: 'clean_hands', name: 'Clean Hands', description: 'Win a game without taking a single disgrace card.',
    icon: '<path d="M11 3c.6 4.2 2.8 6.4 7 7-4.2.6-6.4 2.8-7 7-.6-4.2-2.8-6.4-7-7 4.2-.6 6.4-2.8 7-7z"/><path d="M19 15.5v4M17 17.5h4"/>' },
  { id: 'sniper', name: 'Sniper', description: 'Win an auction that only ever had one bid -- yours.',
    icon: '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/>'
        + '<path d="M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21"/>' },
  { id: 'free_lunch', name: 'Free Lunch', description: 'Win an auction paying nothing.',
    icon: '<circle cx="12" cy="12" r="8"/><path d="M5 19L19 5"/>' },
  { id: 'minimalist', name: 'Minimalist', description: 'Win an auction for the lowest-value Painting.',
    icon: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/>' },
  { id: 'masterpiece', name: 'Masterpiece', description: 'Win the 10-value Painting.',
    icon: '<rect x="3.5" y="5" width="17" height="14" rx="1"/><rect x="6.5" y="8" width="11" height="8"/>'
        + '<path d="M6.5 14.5l3-3 2.5 2.5 2-2 3.5 3.5"/>' },
  { id: 'high_roller', name: 'High Roller', description: 'Pay 20 or more for a single card.',
    icon: '<rect x="4" y="4" width="16" height="16" rx="3"/>'
        + '<circle cx="8.5" cy="8.5" r="1.1" fill="currentColor" stroke="none"/>'
        + '<circle cx="15.5" cy="8.5" r="1.1" fill="currentColor" stroke="none"/>'
        + '<circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none"/>'
        + '<circle cx="8.5" cy="15.5" r="1.1" fill="currentColor" stroke="none"/>'
        + '<circle cx="15.5" cy="15.5" r="1.1" fill="currentColor" stroke="none"/>' },
  { id: 'narrow_escape', name: 'Narrow Escape', description: 'Escape elimination with just 1 more money than the poorest player.',
    icon: '<path d="M6.5 3h11M6.5 21h11"/><path d="M7.5 3c0 5 4.5 6 4.5 9s-4.5 4-4.5 9"/><path d="M16.5 3c0 5-4.5 6-4.5 9s4.5 4 4.5 9"/>' },
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
  // Losing has its moments too.
  { id: 'good_sport', name: 'Good Sport', description: "Finish a game you didn't win.",
    icon: '<path d="M7 11v9H4v-9z"/><path d="M7 11l4-7c1.5 0 2.5 1 2.2 2.6L12.6 10H18a2 2 0 0 1 2 2.3l-1 6A2 2 0 0 1 17 20H7"/>' },
  { id: 'thick_skin', name: 'Thick Skin', description: 'Lose 10 games.',
    icon: '<rect x="3" y="5" width="18" height="14" rx="1"/>'
        + '<path d="M3 9.7h18M3 14.3h18M9 5v4.7M15 5v4.7M6 9.7v4.6M12 9.7v4.6M18 9.7v4.6M9 14.3V19M15 14.3V19"/>' },
  { id: 'so_close', name: 'So Close', description: 'Lose a game by a single point.',
    icon: '<path d="M12 4v16"/><path d="M4 12h5M15 12h5"/><path d="M7 9.5L9.5 12 7 14.5M17 9.5L14.5 12 17 14.5"/>' },
  { id: 'spent_it_all', name: 'Spent It All', description: 'Score the most points at the table, then get eliminated for having the least money.',
    icon: '<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4"/>'
        + '<path d="M12 13v3.5M9 20h6"/><path d="M11 4.5l2 2.5-1.5 2"/>' },
  { id: 'bankrupt', name: 'Bankrupt', description: 'Finish a game with no money left.',
    icon: '<path d="M4 7.5A2.5 2.5 0 0 1 6.5 5H18v2.5"/><rect x="4" y="7.5" width="16" height="12" rx="2"/><path d="M16 13.5h4"/>' },
  { id: 'wallflower', name: 'Wallflower', description: 'Finish a game without winning a single auction.',
    icon: '<circle cx="12" cy="8.5" r="1.6"/><circle cx="12" cy="4.8" r="1.9"/><circle cx="15.7" cy="8.5" r="1.9"/>'
        + '<circle cx="8.3" cy="8.5" r="1.9"/><circle cx="12" cy="12.2" r="1.9"/>'
        + '<path d="M12 14.1V21M12 18.5c-1.5-2-3.5-2.5-5-2 .5 1.8 2.5 2.7 5 2"/>' },
  { id: 'social_pariah', name: 'Social Pariah', description: 'Take a Faux Pas, a Passe and a Scandale in one game.',
    icon: '<circle cx="10" cy="8" r="3.2"/><path d="M4 20c0-3.4 2.7-6 6-6 1.3 0 2.5.4 3.4 1"/><path d="M16 15l4 4M20 15l-4 4"/>' },
  { id: 'in_the_red', name: 'In the Red', description: 'Finish a game with a negative score.',
    icon: '<path d="M3 6l6 6 4-4 8 8"/><path d="M15 16h6v-6"/>' },
];

// Same lock glyph used both as the front's small icon-badge pin and (per
// feedback) as the back face's own locked indicator, instead of a text
// "Locked" label -- one glyph, one meaning, wherever it shows up.
const LOCK_ICON_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>
    </svg>`;

// A locked tile's own icon badge gets a small lock pin (rather than
// leaving "why is this grayed out" to be inferred from opacity alone).
// Clicking (or Enter/Space while focused -- see onAchievementTileClick/
// onAchievementTileKeydown in app.js) flips the tile to a back face with
// the full description -- replaces the old hover/focus tooltip, which
// never worked on a touch device at all and, per feedback, disappeared
// before it could reliably be read. tabindex so a keyboard user can reach
// the same flip via focus+Enter/Space; aria-label still carries the same
// name+description for screen readers regardless of flip state.
//
// The back face deliberately mirrors the front's own locked/unlocked
// treatment (same cream surface, same grayscale+faded-vs-full-color split,
// same lock glyph) rather than a separately-colored "reveal" surface --
// per explicit feedback, a different color scheme on the back read as
// off-palette rather than "the same tile, turned around".
function renderAchievementTile(a, unlocked) {
  const lockBadge = unlocked ? '' : `<span class="achievement-lock">${LOCK_ICON_SVG}</span>`;
  const backLock = unlocked ? '' : `<span class="achievement-tile-back-lock">${LOCK_ICON_SVG}</span>`;
  return `<div class="achievement-tile ${unlocked ? 'unlocked' : 'locked'}" tabindex="0" aria-label="${escapeHtml(a.name)}: ${escapeHtml(a.description)}">`
    + `<div class="achievement-tile-inner">`
    + `<div class="achievement-tile-face achievement-tile-front">`
    + `<span class="achievement-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" `
    + `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${a.icon}</svg>${lockBadge}</span>`
    + `<span class="achievement-name">${escapeHtml(a.name)}</span>`
    + `</div>`
    + `<div class="achievement-tile-face achievement-tile-back" aria-hidden="true">`
    + backLock
    + `<strong>${escapeHtml(a.name)}</strong>`
    + `<p>${escapeHtml(a.description)}</p>`
    + `</div>`
    + `</div></div>`;
}

// Delegated on #achievements-grid (app.js) -- toggling .flipped rather than
// tracking open state anywhere in JS keeps this a pure, stateless view of
// whatever's currently in the DOM; a full re-render (a live unlock landing
// while this screen happens to be open) just resets any flipped tiles back
// to their front face, an acceptable rare-case trade-off.
export function onAchievementTileClick(e) {
  const tile = e.target.closest('.achievement-tile');
  if (tile) tile.classList.toggle('flipped');
}

export function onAchievementTileKeydown(e) {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const tile = e.target.closest('.achievement-tile');
  if (!tile) return;
  e.preventDefault(); // Space must not also scroll the page
  tile.classList.toggle('flipped');
}

// Fired once as soon as boot knows who's signed in (see login.js's
// proceedPastLogin) rather than waiting for the user to actually click
// into Account -- the query itself is cheap, but it's still a real round
// trip to remote Supabase (2-3s isn't unusual, see this session's own
// production timing notes), so front-loading it while they're still
// browsing the home screen removes that wait from the moment they open
// Account. Keyed by username so a mid-session identity change (login/
// logout/username edit) doesn't serve a stale prefetch for someone else.
//
// Now backed by utils/swrCache.js: the stats for any username (yours on
// Account and the Home Elo badge, anyone's on their public profile) are one
// cache entry that survives reloads. Every caller paints `cached` at once
// and re-renders on `freshPromise`, so a finished game is never hidden
// behind stale numbers for longer than one round trip.
export function profileStats(username) {
  return swr(`profile:${username}`, () => fetchJSON(`/api/profile/${encodeURIComponent(username)}`));
}

// Fresh stats when the server answers, else the last cached copy -- for
// callers that need one answer rather than a two-step paint (see
// lobby.js's refreshTutorialCta).
export async function getPrefetchedStats(username) {
  const { cached, freshPromise } = profileStats(username);
  return (await freshPromise) || cached;
}

// Called right after login (and after each game, see rematch.js): warms the
// cache for everything the signed-in player is likely to open next, all in
// parallel and in the background, so Account, Achievements, My Games, the
// Leaderboard and Home's own widgets open with real data already in place.
export function prefetchAccountStats() {
  const profile = loadProfile();
  if (!profile) return;
  profileStats(profile.username);
  fetchGamesPage(profile.username, 0);
  fetchRatingHistory(`/api/profile/${encodeURIComponent(profile.username)}/rating_history`);
  if (profile.google_id) fetchAchievements(profile.username);
  import('../lobby/leaderboard.js').then((m) => m.fetchLeaderboardPage(0)).catch(() => {});
  import('../lobby/lobby.js').then((m) => m.fetchGlobalStats()).catch(() => {});
}

// account-stats-row.loading CSS) the instant the screen opens, then
// swapped for real numbers once /api/profile resolves -- previously the
// whole row was just `hidden` until the fetch finished, so the cards
// appeared out of nowhere rather than looking like they were loading.
// Stats aren't gated to Google-linked accounts (get_player_profile_stats
// isn't either) -- a guest with no games yet gets the "—" defaults
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

  const dateLabel = (iso) => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  $('account-player-since').textContent = dateLabel(stats.created_at);
  // "Last played", not live presence -- see get_player_profile_stats' own
  // comment on why last_seen_at only updates when a game finishes, not on
  // every visit. timeAgo reads more like an actual "last active" glance
  // (e.g. "2h ago") than a bare date would for anything recent.
  $('account-last-played').textContent = timeAgo(stats.last_played_at);
  $('account-meta-row').classList.remove('loading');
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
  const { cached, freshPromise } = profileStats(profile.username);
  if (cached) renderAccountStats(cached, profile);
  const fresh = await freshPromise;
  if (fresh) {
    renderAccountStats(fresh, profile);
  } else if (!cached) {
    // A 404 (no games finished yet -- the players row only appears once
    // one does) or a failed fetch: settle into the cards' "—" defaults,
    // the same as a Player Profile does, rather than pulsing forever.
    row.classList.remove('loading');
    $('account-meta-row').classList.remove('loading');
  }
}

// ------------------------------------------------------------ Elo chart --
//
// The actual ApexCharts implementation lives in ui/eloChart.js now,
// shared with a public Player Profile's own identical chart (see
// lobby/playerProfile.js) -- this screen just owns one controller
// instance pointed at its own ids. No sectionId any more -- the chart's
// own tile is a permanent part of the layout now (see index.html's own
// comment on .account-stats-chart-row), never hidden as a whole.
const eloChartController = createEloChartController({
  containerId: 'account-elo-chart',
  rangeToggleId: 'account-elo-chart-range-toggle',
});
export function onEloChartRangeClick(e) { eloChartController.onRangeClick(e); }

// Guests never accrue real rating history (record_finished_game only
// rates google_id-linked players -- same gate account-elo's own Unrated
// label already uses), so there's nothing meaningful to plot -- shows a
// plain explanation in the chart's own slot instead of an empty chart
// (or, as before this screen reserved its layout permanently, hiding
// the whole tile).
async function loadEloChart(profile) {
  if (!profile || !profile.google_id) {
    eloChartController.showUnavailable('Connect Google to start tracking your Elo rating.');
    return;
  }
  await eloChartController.load(`/api/profile/${encodeURIComponent(profile.username)}/rating_history`, {
    emptyMessage: 'No rated games yet.',
  });
}

// -------------------------------------------------------- recent activity --
//
// Renders through gameHistory.js's own renderIfChanged/gamesListHtml --
// the exact same row markup Home's Recent Games widget and the My Games
// screen use -- rather than a bespoke design of its own, per explicit
// feedback that this used to look like a visually different component.
// Also shares fetchGamesPage's own cache with that same widget rather
// than a separate fetch of its own -- Home is the app's own landing
// screen, so by the time anyone opens Account this data has almost
// always already been fetched and cached this session, making this paint
// instantly with zero added network wait. Only a genuinely first-ever
// fetch this session (e.g. a deep link straight to /account) pays for a
// real round trip, same as Home's own widget would have anyway.
async function loadRecentActivity(profile) {
  const list = $('account-recent-activity-list');
  const empty = $('account-recent-activity-empty');
  if (!profile) { list.classList.remove('tile-loading-spinner'); show(empty); return; }
  const paint = (page) => {
    const games = page.games.slice(0, 5);
    list.classList.remove('tile-loading-spinner');
    if (games.length === 0) { list.innerHTML = ''; show(empty); return; }
    hide(empty);
    renderIfChanged(list, games, profile.username);
  };
  const { cached, freshPromise } = fetchGamesPage(profile.username, 0);
  if (cached) paint(cached);
  const fresh = await freshPromise;
  if (fresh) paint(fresh);
  else if (!cached) { list.classList.remove('tile-loading-spinner'); show(empty); }
}

// Cached per username, in memory for this tab's lifetime -- an unlocked
// achievement never re-locks (see achievements.py's own append-only
// design: "new achievements just unlock the new ones, existing ones stay
// correct"), so a cached set is never wrong, only possibly missing
// something unlocked moments ago. Reported live as "the achievements
// page loads slowly": every visit re-painted the whole grid as fully
// locked *first* (see the old unconditional grid.innerHTML reset below)
// before the real fetch landed, even for a returning visitor who'd
// already unlocked half of them -- that's what actually read as slow,
// not the network call itself.
// Unlocked achievement ids for `username`, cached across reloads (see
// utils/swrCache.js) -- shared by Achievements and the login-time prefetch.
function fetchAchievements(username) {
  return swr(`achievements:${username}`, () =>
    fetchJSON(`/api/achievements?username=${encodeURIComponent(username)}`).then((r) => r.achievements || []));
}

function renderAchievementsIfChanged(grid, unlocked) {
  const html = ACHIEVEMENTS.map((a) => renderAchievementTile(a, unlocked.has(a.id))).join('');
  if (grid.dataset.renderedHtml === html) return; // identical to what's already shown -- skip the repaint
  grid.innerHTML = html;
  grid.dataset.renderedHtml = html;
}

// Guests have no stable identity to hang persistent progress off (a
// guest's username is regenerated every time their browser profile
// resets -- see saveProfile), so achievements only ever start counting
// once someone is actually signed in with Google (see
// game_history.record_finished_game's google_id gating). Every tile still
// renders, just permanently locked, with a sign-in note above the grid.
async function loadAchievements() {
  const profile = loadProfile();
  const grid = $('achievements-grid');
  const guestNote = $('account-achievements-guest-note');
  if (!profile || !profile.google_id) {
    renderAchievementsIfChanged(grid, new Set());
    if (!profile) return;
    show(guestNote); // the screen's own note -- a toast saying the same thing on top of it was a double nag
    $('achievements-connect-google').classList.toggle('hidden', !googleSignInConfigured());
    return;
  }
  hide(guestNote);
  const { cached, freshPromise } = fetchAchievements(profile.username);
  // Cached: instant repaint from the last known-good state -- no
  // locked-then-unlocked flash. Never seen on this browser: the locked
  // skeleton stands in as "loading".
  renderAchievementsIfChanged(grid, new Set(cached || []));
  const fresh = await freshPromise;
  if (fresh) renderAchievementsIfChanged(grid, new Set(fresh));
  // null: transient network error -- leave whatever's already rendered
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
  $('account-connect-google').classList.toggle('hidden', !(profile && !profile.google_id && googleSignInConfigured()));
  hide($('account-username-edit')); // collapsed by default -- see onAccountEditUsernameClick
  hide($('account-error'));
  hide($('account-saved'));
  $('account-meta-row').classList.add('loading');
  // Neither the Elo chart nor Game History are ever hidden as a whole any
  // more (see .account-stats-chart-row's own comment in index.html) --
  // both tiles are a permanent part of this screen's layout, and this
  // just resets their own *internal* loading state for a fresh profile
  // (logging out of a Google account with real data, back in as a fresh
  // guest, shouldn't flash the previous profile's chart/feed for a
  // moment before the new one's own load overwrites it).
  $('account-elo-chart').classList.add('tile-loading-spinner');
  $('account-recent-activity-list').classList.add('tile-loading-spinner');
  hide($('account-recent-activity-empty'));
  showScreen('screen-account');
  // Includes the username (chess.com-style) purely for a nicer/shareable
  // URL -- this is never a per-user viewer, so a stale or foreign
  // username left over in the address bar (an old bookmark after a
  // rename, say) just gets silently corrected to whoever's actually
  // logged in on the very next visit, same as everything else this
  // screen renders. Falls back to the bare path if somehow reached
  // with no profile yet.
  setScreenPath(profile ? `/account/${encodeURIComponent(profile.username)}` : '/account');
  // All three run independently/in parallel, same as the stats prefetch
  // already did -- none of them awaits or blocks another, so the screen's
  // fast initial paint (stats, from the login-time prefetch) is never held
  // up waiting on the chart or activity feed to resolve.
  loadAccountStats();
  loadEloChart(profile);
  loadRecentActivity(profile);
}

// The pencil next to the username -- reveals the same input+Save that
// used to just sit there permanently open. Re-syncs the input to the
// current saved value every time (not just on showAccountScreen) so
// re-opening after a Cancel never shows a stale in-progress edit.
let _renameField = null;
function renameField() {
  if (!_renameField) {
    _renameField = attachUsernameField({
      field: $('account-username-field'),
      input: $('account-username-input'),
      status: $('account-username-status'),
      count: $('account-username-count'),
      submit: $('btn-account-save'),
      current: () => (loadProfile() || {}).username || null,
    });
  }
  return _renameField;
}

export function onAccountEditUsernameClick() {
  const profile = loadProfile();
  $('account-username-input').value = profile ? profile.username : '';
  hide($('account-error'));
  hide($('account-saved'));
  show($('account-username-edit'));
  renameField().check();
  $('account-username-input').focus();
  $('account-username-input').select();
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
  const field = renameField();
  if ($('btn-account-save').classList.contains('is-busy')) return;
  if (!(await field.ready())) return;
  const username = $('account-username-input').value.trim();
  const existing = loadProfile();
  if (existing && existing.username === username) {
    hide($('account-username-edit'));
    show($('account-saved'));
    return;
  }
  field.setBusy(true, 'Saving…');
  try {
    const result = await fetchJSON('/api/auth/username/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        old_username: existing ? existing.username : username, new_username: username, login_token: loginToken(),
      }),
    });
    saveProfile(result.username, result.username);
    renderProfileChip();
    $('account-username-display').textContent = result.username;
    // Collapses back to plain text -- an error, by contrast, leaves this
    // open (see the catch below) so there's something left to fix.
    hide($('account-username-edit'));
    show($('account-saved'));
  } catch (e) {
    if (e.status === 409) field.showTaken(e.body && e.body.suggestion);
    else showError($('account-error'), e.message);
  } finally {
    field.setBusy(false, 'Save');
  }
}
