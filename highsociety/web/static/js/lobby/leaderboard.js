// Leaderboard screen: ranked table (Google-linked accounts only -- see
// game_history.py's get_leaderboard) plus a hand-rolled inline-SVG
// sparkline of the signed-in player's own rating history. No charting
// library: a sparkline here is just a <polyline> built from an array of
// points, computed once when this screen opens, not a render loop -- see
// this session's own cost discussion, it's negligible either way.
import { $, hide, show, showScreen, setScreenPath } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { loadProfile } from '../auth/profile.js';
import { fetchJSON } from './lobby.js';

const SPARKLINE_WIDTH = 260;
const SPARKLINE_HEIGHT = 48;

// `history`: [{old_rating, new_rating, created_at}, ...] oldest first
// (see get_rating_history). Plots every new_rating, prefixed by the very
// first entry's old_rating so the line actually starts somewhere instead
// of jumping in mid-air on its first point.
function renderSparkline(history) {
  if (history.length === 0) {
    return '<p class="muted">No rated games yet.</p>';
  }
  const values = [history[0].old_rating, ...history.map((h) => h.new_rating)];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1; // avoid a divide-by-zero flat line
  const points = values.map((v, i) => {
    const x = (i / (values.length - 1 || 1)) * SPARKLINE_WIDTH;
    const y = SPARKLINE_HEIGHT - ((v - min) / range) * SPARKLINE_HEIGHT;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const trendUp = values[values.length - 1] >= values[0];
  return `
    <svg viewBox="0 0 ${SPARKLINE_WIDTH} ${SPARKLINE_HEIGHT}" class="sparkline-svg" preserveAspectRatio="none">
      <polyline points="${points}" fill="none" stroke="${trendUp ? 'var(--chip-money)' : 'var(--danger)'}" stroke-width="2"/>
    </svg>
    <div class="sparkline-range"><span>${min}</span><span>${max}</span></div>
  `;
}

export async function showLeaderboardScreen() {
  showScreen('screen-leaderboard');
  setScreenPath('/leaderboard');
  const body = $('leaderboard-body');
  const empty = $('leaderboard-empty');
  body.innerHTML = '';
  hide(empty);

  const profile = loadProfile();
  const myRatingSection = $('leaderboard-my-rating');
  if (profile && profile.google_id) {
    try {
      const result = await fetchJSON(`/api/profile/${encodeURIComponent(profile.username)}/rating_history`);
      $('leaderboard-sparkline').innerHTML = renderSparkline(result.history || []);
      show(myRatingSection);
    } catch (e) {
      hide(myRatingSection);
    }
  } else {
    hide(myRatingSection); // guests have no rating history to show
  }

  try {
    const result = await fetchJSON('/api/leaderboard');
    const rows = result.leaderboard || [];
    if (rows.length === 0) { show(empty); return; }
    body.innerHTML = rows.map((r, i) => `
      <tr class="${profile && r.username === profile.username ? 'leaderboard-row-me' : ''}">
        <td>${i + 1}</td>
        <td>${escapeHtml(r.username)}</td>
        <td>${r.elo}</td>
        <td>${r.games_played}</td>
        <td>${r.games_won}</td>
      </tr>
    `).join('');
  } catch (e) {
    show(empty);
  }
}
