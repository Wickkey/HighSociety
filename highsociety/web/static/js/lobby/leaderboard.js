// Leaderboard screen: ranked table (Google-linked accounts only -- see
// game_history.py's get_leaderboard) plus the signed-in player's own
// rating-history chart, drawn with Chart.js -- the hand-rolled inline-SVG
// <polyline> sparkline this used to be had nowhere to grow (no axes, no
// gridlines, no hover detail) and reportedly looked rough. Chart.js is
// vendored locally as a plain UMD bundle (highsociety/web/static/js/
// vendor/chart.umd.min.js) rather than pulled from a CDN or given a
// build step: it self-registers window.Chart from a classic <script>,
// loaded lazily (see loadChartJs below) only once this screen is
// actually opened -- every other screen never pays for its ~200KB.
import { $, hide, show, showScreen, setScreenPath } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { loadProfile } from '../auth/profile.js';
import { fetchJSON } from './lobby.js';

let chartJsLoadPromise = null;

function loadChartJs() {
  if (window.Chart) return Promise.resolve();
  if (!chartJsLoadPromise) {
    chartJsLoadPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/js/vendor/chart.umd.min.js';
      script.onload = resolve;
      script.onerror = () => { chartJsLoadPromise = null; reject(new Error('Failed to load chart.js')); };
      document.head.appendChild(script);
    });
  }
  return chartJsLoadPromise;
}

// A CSS custom property's raw value (e.g. "#1f7a4d") -- Chart.js draws on
// a plain <canvas>, whose 2D context has no idea what a CSS variable is,
// so every color handed to it has to already be a resolved literal.
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

let ratingChart = null; // the live Chart.js instance, if any -- see renderSparkline

// `history`: [{old_rating, new_rating, created_at}, ...] oldest first
// (see get_rating_history). Plots every new_rating, prefixed by the very
// first entry's old_rating so the line actually starts somewhere instead
// of jumping in mid-air on its first point.
//
// A real time-scale x-axis (Chart.js's own "time" axis, fed the point's
// actual `created_at`) rather than evenly-spaced-by-index -- an
// index-based layout would spread a burst of several games played in one
// evening across the *entire* width, identical to if they'd been spread
// across months, which is what made the old sparkline look like a
// meaningless zigzag rather than an actual rating history (a real
// reported issue this preserves the fix for). The prefixed old_rating
// point sits at a synthetic time just before the first real game (5% of
// the whole span back) purely so the line has a visible starting slope
// -- it's never a real game's own timestamp.
async function renderSparkline(history) {
  const container = $('leaderboard-sparkline');
  if (ratingChart) { ratingChart.destroy(); ratingChart = null; }
  if (history.length === 0) {
    container.innerHTML = '<p class="muted">No rated games yet.</p>';
    return;
  }
  container.innerHTML = '<canvas id="leaderboard-sparkline-canvas"></canvas>';
  try {
    await loadChartJs();
  } catch (e) {
    container.innerHTML = '<p class="muted">Rating history unavailable right now.</p>';
    return;
  }

  const gameTimes = history.map((h) => new Date(h.created_at).getTime());
  const span = gameTimes[gameTimes.length - 1] - gameTimes[0] || 1;
  const times = [gameTimes[0] - span * 0.05, ...gameTimes];
  const values = [history[0].old_rating, ...history.map((h) => h.new_rating)];
  const trendUp = values[values.length - 1] >= values[0];
  const lineColor = trendUp ? cssVar('--chip-money') : cssVar('--danger');
  const gridColor = cssVar('--panel-border');
  const textColor = cssVar('--muted');

  const dateLabel = (ms) => new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });

  const ctx = $('leaderboard-sparkline-canvas').getContext('2d');
  ratingChart = new window.Chart(ctx, {
    type: 'line',
    data: {
      datasets: [{
        data: times.map((t, i) => ({ x: t, y: values[i] })),
        borderColor: lineColor,
        backgroundColor: lineColor,
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 2,
        tension: 0.25,
        fill: false,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      interaction: { intersect: false, mode: 'nearest', axis: 'x' },
      scales: {
        // A plain linear scale over raw millisecond timestamps, not
        // Chart.js's own "time" scale -- that needs a separate date-
        // adapter package (chartjs-adapter-date-fns or similar) this repo
        // doesn't vendor. Formatting tick/tooltip labels by hand below
        // gets the same result without that extra dependency.
        x: {
          type: 'linear',
          grid: { display: false },
          ticks: { color: textColor, maxRotation: 0, autoSkipPadding: 16, callback: dateLabel },
        },
        y: {
          grid: { color: gridColor },
          ticks: { color: textColor, precision: 0 },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (items) => dateLabel(items[0].parsed.x),
            label: (item) => `Rating: ${Math.round(item.parsed.y)}`,
          },
        },
      },
    },
  });
}

// loadMyRatingChart/renderSparkline/loadChartJs below are deliberately
// kept even though nothing on this screen calls them anymore -- the
// leaderboard is the exact same data for every visitor and should load
// instantly (see get_leaderboard's own caching for the backend half of
// that), which a per-player chart fetch sharing the screen had no
// business slowing down or blocking on. The chart itself isn't gone,
// just relocated to wherever it's asked to move to next.
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
    body.innerHTML = rows.map((r, i) => `
      <tr class="${profile && r.username === profile.username ? 'leaderboard-row-me' : ''}">
        <td>${offset + i + 1}</td>
        <td>${escapeHtml(r.username)}</td>
        <td>${r.elo}</td>
        <td>${r.games_played}</td>
        <td>${r.games_won}</td>
      </tr>
    `).join('');
    $('btn-leaderboard-prev').disabled = offset === 0;
    $('btn-leaderboard-next').disabled = !result.has_more;
    $('leaderboard-page-label').textContent = `${offset + 1}–${offset + rows.length}`;
    show(pagination);
  } catch (e) {
    body.innerHTML = '';
    show(empty);
  }
}

async function loadMyRatingChart(profile) {
  try {
    const result = await fetchJSON(`/api/profile/${encodeURIComponent(profile.username)}/rating_history`);
    $('leaderboard-sparkline').classList.remove('loading');
    await renderSparkline(result.history || []);
  } catch (e) {
    hide($('leaderboard-my-rating'));
  }
}
