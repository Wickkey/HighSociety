// Leaderboard screen: ranked table (Google-linked accounts only -- see
// game_history.py's get_leaderboard). Used to also own the signed-in
// player's own Chart.js rating-history chart, but that was pulled back
// out at the user's own request to relocate elsewhere -- it now lives on
// the Account screen instead (see account.js's loadEloChart), which is
// where loadChartJs/renderSparkline/loadMyRatingChart moved to.
import { $, hide, show, showScreen, setScreenPath } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { loadProfile } from '../auth/profile.js';
import { fetchJSON } from './lobby.js';
import { swr } from '../utils/swrCache.js';
import { showPlayerProfileScreen } from './playerProfile.js';

const PAGE_SIZE = 20;
let leaderboardOffset = 0;

export function showLeaderboardScreen() {
  showScreen('screen-leaderboard');
  setScreenPath('/leaderboard');
  leaderboardOffset = 0;
  loadLeaderboardPage(loadProfile());
}

export function onLeaderboardPrevClick() {
  leaderboardOffset = Math.max(0, leaderboardOffset - PAGE_SIZE);
  loadLeaderboardPage(loadProfile());
}

export function onLeaderboardNextClick() {
  leaderboardOffset += PAGE_SIZE;
  loadLeaderboardPage(loadProfile());
}

// One leaderboard page, cached across reloads (utils/swrCache.js) --
// exported so the login-time prefetch (account.js) can warm page 1.
export function fetchLeaderboardPage(offset) {
  return swr(`leaderboard:${PAGE_SIZE}:${offset}`, () =>
    fetchJSON(`/api/leaderboard?limit=${PAGE_SIZE}&offset=${offset}`));
}

async function loadLeaderboardPage(profile) {
  const body = $('leaderboard-body');
  const empty = $('leaderboard-empty');
  const pagination = $('leaderboard-pagination');
  const offset = leaderboardOffset; // snapshot -- see the stale-response guard below
  hide(empty);
  const { cached, freshPromise } = fetchLeaderboardPage(offset);
  if (cached && (cached.leaderboard || []).length) {
    paintLeaderboardPage(cached, offset, profile); // last known standings, instantly -- refreshed just below
  } else {
    hide(pagination);
    body.innerHTML = '<tr class="leaderboard-loading-row"><td colspan="5">Loading…</td></tr>';
    delete body.dataset.renderedHtml;
  }
  const result = await freshPromise;
  if (offset !== leaderboardOffset) return; // Prev/Next clicked again before this resolved -- a newer request already owns the screen
  if (!result) {
    if (!cached) { body.innerHTML = ''; delete body.dataset.renderedHtml; show(empty); }
    return; // transient failure -- keep the cached page on screen
  }
  const rows = result.leaderboard || [];
  if (rows.length === 0) {
    body.innerHTML = ''; delete body.dataset.renderedHtml;
    hide(pagination);
    // An empty *first* page really means "no ranked players yet"; an
    // empty later page means Prev/Next raced past the real end (should
    // only happen if the leaderboard shrank between page loads) --
    // either way, back off to the last page that actually had rows
    // rather than stranding the visitor on a blank one.
    if (offset === 0) { show(empty); return; }
    leaderboardOffset = Math.max(0, offset - PAGE_SIZE);
    loadLeaderboardPage(profile);
    return;
  }
  paintLeaderboardPage(result, offset, profile);
}

function paintLeaderboardPage(result, offset, profile) {
  const rows = result.leaderboard || [];
  const html = rows.map((r, i) => {
    const rank = offset + i + 1;
    // Gold / silver / bronze for the top 3, same medal language as the
    // recent-games list's own placement pills (see .recent-game-placement-*).
    const rankClass = rank <= 3 ? ` leaderboard-rank-${rank}` : '';
    return `
      <tr class="${profile && r.username === profile.username ? 'leaderboard-row-me' : ''}">
        <td class="leaderboard-rank-cell${rankClass}"><span class="leaderboard-rank-num">${rank}</span></td>
        <td><button type="button" class="name-link" data-username="${escapeHtml(r.username)}">${escapeHtml(r.username)}</button></td>
        <td>${r.elo}</td>
        <td>${r.games_played}</td>
        <td>${r.games_won}</td>
      </tr>
    `;
  }).join('');
  const body = $('leaderboard-body');
  if (body.dataset.renderedHtml !== html) { // cached and fresh usually match -- skip the identical repaint
    body.innerHTML = html;
    body.dataset.renderedHtml = html;
  }
  $('btn-leaderboard-prev').disabled = offset === 0;
  $('btn-leaderboard-next').disabled = !result.has_more;
  $('leaderboard-page-label').textContent = `${offset + 1}–${offset + rows.length}`;
  show($('leaderboard-pagination'));
}

// Every ranked player's name opens their real public profile page --
// "self-explanatory" per the original ask, since this table is already
// nothing but real, rated humans (see get_leaderboard's own docstring on
// why guests/bots are excluded here).
export function onLeaderboardTableClick(e) {
  const btn = e.target.closest('[data-username]');
  if (btn) showPlayerProfileScreen(btn.dataset.username, 'leaderboard');
}
