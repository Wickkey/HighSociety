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

async function loadLeaderboardPage(profile) {
  const body = $('leaderboard-body');
  const empty = $('leaderboard-empty');
  const pagination = $('leaderboard-pagination');
  const offset = leaderboardOffset; // snapshot -- see the stale-response guard below
  hide(empty);
  hide(pagination);
  body.innerHTML = '<tr class="leaderboard-loading-row"><td colspan="5">Loading…</td></tr>';
  try {
    const result = await fetchJSON(`/api/leaderboard?limit=${PAGE_SIZE}&offset=${offset}`);
    if (offset !== leaderboardOffset) return; // Prev/Next clicked again before this resolved -- a newer request already owns the screen
    const rows = result.leaderboard || [];
    if (rows.length === 0) {
      body.innerHTML = '';
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
    body.innerHTML = rows.map((r, i) => {
      const rank = offset + i + 1;
      return `
      <tr class="${profile && r.username === profile.username ? 'leaderboard-row-me' : ''}">
        <td class="${rank === 1 ? 'leaderboard-rank-1' : ''}">${rank}</td>
        <td>${escapeHtml(r.username)}</td>
        <td>${r.elo}</td>
        <td>${r.games_played}</td>
        <td>${r.games_won}</td>
      </tr>
    `;
    }).join('');
    $('btn-leaderboard-prev').disabled = offset === 0;
    $('btn-leaderboard-next').disabled = !result.has_more;
    $('leaderboard-page-label').textContent = `${offset + 1}–${offset + rows.length}`;
    show(pagination);
  } catch (e) {
    body.innerHTML = '';
    show(empty);
  }
}
