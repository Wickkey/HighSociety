// Public, read-only player profile screen -- any human player, or one of
// the 3 reserved bot identities (see web_server.py's BOT_PROFILES).
// Deliberately separate from account.js's own screen: /account only ever
// shows whoever's logged in on this browser and lets them edit it; this
// one is a pure viewer of *anyone*, reached by clicking a name on the
// Leaderboard (leaderboard.js) or in the game detail modal
// (ui/modals.js), with no edit affordances at all.
import { $, hide, show, showScreen, setScreenPath } from '../utils/dom.js';
import { timeAgo } from '../utils/formatting.js';
import { fetchJSON } from './lobby.js';
import { fetchGamesPage, renderIfChanged } from './gameHistory.js';
import { createEloChartController } from '../ui/eloChart.js';

// Same shared chart Account's own screen uses (see ui/eloChart.js), a
// separate controller instance so this screen's chart state (fetched
// history, live chart object) is never confused with Account's own.
const eloChartController = createEloChartController({
  containerId: 'player-profile-elo-chart',
  sectionId: 'player-profile-elo-chart-section',
  rangeToggleId: 'player-profile-elo-chart-range-toggle',
});
export function onPlayerProfileEloChartRangeClick(e) { eloChartController.onRangeClick(e); }

const PAGE_SIZE = 10; // matches gameHistory.js's own fixed page size -- fetchGamesPage bakes this in already

const dateLabel = (iso) => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });

let profileUsername = null; // the profile currently shown/loading -- stale-response guard for both loaders below
let profileOffset = 0;
// Where the Back button returns to -- either the literal string
// 'leaderboard' (clicked a name directly on the Leaderboard), or the id
// of whichever screen was actually showing underneath when the game
// detail modal that led here was opened (see ui/modals.js's
// openGameDetailModal, which captures that id the instant it opens --
// Home, My Games, and Account can all open that same modal, and each
// needs its own real screen back, not always Leaderboard, which was a
// real reported bug: opening a game from Home and clicking a name in it
// landed back on Leaderboard, not Home). app.js's Back handler is what
// actually turns a screen id back into "how do I re-show that screen".
let profileReturnTo = 'leaderboard';

export function getPlayerProfileReturnTo() {
  return profileReturnTo;
}

export function showPlayerProfileScreen(username, returnTo = 'leaderboard') {
  profileUsername = username;
  profileOffset = 0;
  profileReturnTo = returnTo;
  showScreen('screen-player-profile');
  setScreenPath(`/players/${encodeURIComponent(username)}`);

  $('player-profile-avatar').textContent = username.charAt(0).toUpperCase();
  $('player-profile-name').textContent = username;
  hide($('player-profile-bio'));
  hide($('player-profile-meta-row'));
  hide($('player-profile-elo-hero'));
  $('player-profile-stats-row').classList.add('loading');
  hide($('player-profile-elo-chart-section'));
  $('player-profile-elo-chart').classList.add('loading');
  hide($('player-profile-history-section'));
  $('player-profile-history-list').innerHTML = '';
  delete $('player-profile-history-list').dataset.renderedHtml; // a stale renderIfChanged memo from a *different* profile must never suppress this one's first real paint

  // All three run independently -- none awaits or blocks another, same as
  // Account's own screen-open already does. The chart's own relevance
  // guard matters here in a way Account never had to worry about:
  // clicking from one player's profile straight to another's (far more
  // plausible than a login/logout cycle) could otherwise paint a slow
  // first fetch's stale data into a screen that's since moved on.
  loadPlayerProfileStats(username);
  eloChartController.load(`/api/profile/${encodeURIComponent(username)}/rating_history`, () => username === profileUsername);
  loadPlayerProfileHistoryPage();
}

async function loadPlayerProfileStats(username) {
  let stats;
  try {
    stats = await fetchJSON(`/api/profile/${encodeURIComponent(username)}`);
  } catch (e) {
    // No games recorded yet (a brand new guest, or a typo'd username) --
    // a real, honest outcome, not an error: leave the placeholder name/
    // avatar up and the stat cards at their "—" default.
    if (username === profileUsername) $('player-profile-stats-row').classList.remove('loading');
    return;
  }
  if (username !== profileUsername) return; // navigated to a different profile before this resolved

  $('player-profile-avatar').textContent = stats.display_name.charAt(0).toUpperCase();
  $('player-profile-name').textContent = stats.display_name;
  $('player-profile-elo').textContent = stats.elo;
  show($('player-profile-elo-hero'));

  if (stats.is_bot) {
    // A bot never "joined" -- player-since/last-played would just read as
    // "since this table's schema was created", so a bit of flavor text
    // takes that slot instead (see BOT_PROFILES' own comment).
    $('player-profile-bio').textContent = stats.bio;
    show($('player-profile-bio'));
  } else {
    $('player-profile-since').textContent = dateLabel(stats.created_at);
    $('player-profile-last-played').textContent = stats.last_played_at ? timeAgo(stats.last_played_at) : '—';
    show($('player-profile-meta-row'));
  }

  $('player-profile-stat-games').textContent = stats.games_played;
  $('player-profile-stat-wins').textContent = stats.wins;
  $('player-profile-stat-winrate').textContent = `${Math.round(stats.win_rate * 100)}%`;
  $('player-profile-stat-avg-placement').textContent = stats.avg_placement != null ? stats.avg_placement.toFixed(1) : '—';
  $('player-profile-stat-avg-points').textContent = stats.avg_points != null ? stats.avg_points.toFixed(1) : '—';
  $('player-profile-stat-avg-money').textContent = stats.avg_money_remaining != null ? stats.avg_money_remaining.toFixed(1) : '—';
  $('player-profile-stats-row').classList.remove('loading');
}

// Paginated (10 per page, Prev/Next) exactly like the My Games screen --
// "potentially all their games", not just a short teaser -- reusing that
// same fetchGamesPage/renderIfChanged pair (see gameHistory.js's own
// module comment) so this list looks and behaves identically everywhere
// it appears, not a fourth parallel implementation.
async function loadPlayerProfileHistoryPage() {
  const username = profileUsername;
  const offset = profileOffset;
  const { cached, freshPromise } = fetchGamesPage(username, offset);
  if (cached) paintPlayerProfileHistoryPage(cached, username, offset);
  const fresh = await freshPromise;
  if (username !== profileUsername || offset !== profileOffset) return; // Prev/Next (or a whole new profile) already moved on
  if (fresh) paintPlayerProfileHistoryPage(fresh, username, offset);
  else if (!cached) {
    hide($('player-profile-history-section'));
  }
}

function paintPlayerProfileHistoryPage(page, username, offset) {
  const section = $('player-profile-history-section');
  const list = $('player-profile-history-list');
  const empty = $('player-profile-history-empty');
  const pagination = $('player-profile-history-pagination');
  show(section);
  if (page.games.length === 0 && offset === 0) {
    list.innerHTML = '';
    show(empty);
    hide(pagination);
    return;
  }
  hide(empty);
  renderIfChanged(list, page.games, username);
  $('btn-player-profile-history-prev').disabled = offset === 0;
  $('btn-player-profile-history-next').disabled = !page.has_more;
  $('player-profile-history-page-label').textContent = `${offset + 1}–${offset + page.games.length}`;
  show(pagination);
}

export function onPlayerProfileHistoryPrevClick() {
  profileOffset = Math.max(0, profileOffset - PAGE_SIZE);
  loadPlayerProfileHistoryPage();
}

export function onPlayerProfileHistoryNextClick() {
  profileOffset += PAGE_SIZE;
  loadPlayerProfileHistoryPage();
}
