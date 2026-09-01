// Shared ApexCharts-based Elo history chart -- originally Account-only
// (see git history), pulled out here so a public Player Profile can show
// the exact same chart for the profile it's viewing, not a second
// bespoke implementation. A factory (one call per screen instance)
// rather than bare module-level state: Account and a Player Profile need
// entirely independent state (their own fetched history / live chart
// instance), and even within one screen, navigating from one profile to
// another needs a clean slate, not state left over from whoever was
// viewed before.
//
// Deliberately never hides its own container -- a real reported bug:
// this chart used to live in its own tile that stayed hidden until its
// (slower) async fetch resolved, and whenever that took longer than
// whatever sat around it, the chart would pop in *after* the screen had
// already settled, visibly relocating everything below it. The caller
// (account.js/playerProfile.js) now reserves this exact slot up front
// and keeps it reserved permanently; this controller only ever swaps
// what's *inside* the slot between a loading spinner (the container's
// own .loading class, set by the caller before load() is even called),
// the real chart, or a short explanation of why there's nothing to plot
// -- never removing the slot itself.
import { $ } from '../utils/dom.js';
import { fetchJSON } from '../lobby/lobby.js';

// Real axes/gridlines/tooltips via a real charting library, vendored
// locally as a single UMD bundle (highsociety/web/static/js/vendor/
// apexcharts.min.js) rather than pulled from a CDN or given a build
// step -- self-registers window.ApexCharts from a classic <script>,
// loaded lazily only the first time any screen actually needs it, so
// every other screen never pays for it. One shared load promise across
// every controller instance -- the library itself is a single global
// script tag, not something each screen needs its own copy of.
let apexChartsLoadPromise = null;
function loadApexCharts() {
  if (window.ApexCharts) return Promise.resolve();
  if (!apexChartsLoadPromise) {
    apexChartsLoadPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/js/vendor/apexcharts.min.js';
      script.onload = resolve;
      script.onerror = () => { apexChartsLoadPromise = null; reject(new Error('Failed to load apexcharts')); };
      document.head.appendChild(script);
    });
  }
  return apexChartsLoadPromise;
}

// A CSS custom property's raw value (e.g. "#1f7a4d") -- ApexCharts draws
// on inline SVG, which has no idea what a CSS variable is either, so
// every color handed to it has to already be a resolved literal.
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Client-side only -- get_rating_history already returns everything, so
// narrowing to a window is just a filter on timestamps already in hand,
// never a re-fetch.
function filterHistoryByRange(history, range) {
  if (range === 'all') return history;
  const days = range === '7' ? 7 : 30;
  const cutoffMs = Date.now() - days * 24 * 60 * 60 * 1000;
  return history.filter((h) => new Date(h.created_at).getTime() >= cutoffMs);
}

// `containerId`/`rangeToggleId`: the two elements every caller of this
// chart already has (see index.html's Account and Player Profile
// markup, identical structure, different ids) -- a heading with a
// .section-icon sits above both, owned by the caller's own static HTML,
// not this controller.
export function createEloChartController({ containerId, rangeToggleId }) {
  let chart = null; // the live ApexCharts instance, if any
  let fullHistory = []; // the unfiltered fetch, so the 7D/30D/All toggle can re-slice without a new request

  function setRangeToggleEnabled(enabled) {
    document.querySelectorAll(`#${rangeToggleId} .account-elo-chart-range-btn`).forEach((b) => {
      b.disabled = !enabled;
    });
  }

  // A plain message in place of the chart -- used for "nothing to plot"
  // (no rated games at all, a guest who hasn't signed in, ApexCharts
  // itself failing to load) as well as filtered-empty ("no games in
  // this 7D/30D window"). Never touches the container's own visibility,
  // only what's inside it.
  function renderMessage(text) {
    if (chart) { chart.destroy(); chart = null; }
    const container = $(containerId);
    container.classList.remove('tile-loading-spinner');
    container.innerHTML = `<p class="muted account-elo-chart-empty">${text}</p>`;
  }

  // `history`: [{old_rating, new_rating, created_at}, ...] oldest first
  // (see get_rating_history), already narrowed to whatever range is
  // currently selected (see filterHistoryByRange) -- an empty array here
  // means "no games in this window" (renderMessage handles it), not
  // "nothing to plot at all" (load's own guard below handles that case
  // before this is ever called). Plots every new_rating, prefixed by the
  // very first entry's old_rating so the line actually starts somewhere
  // instead of jumping in mid-air on its first point.
  //
  // A real datetime x-axis (each point keeps its actual timestamp)
  // rather than evenly-spaced-by-index -- an index-based layout would
  // spread a burst of several games played in one evening across the
  // *entire* width, identical to if they'd been spread across months,
  // which is what made the old sparkline look like a meaningless zigzag
  // rather than an actual rating history. The prefixed old_rating point
  // sits at a synthetic time just before the first real game (5% of the
  // whole span back) purely so the line has a visible starting slope --
  // it's never a real game's own timestamp.
  async function render(history) {
    if (history.length === 0) { renderMessage('No games in this range.'); return; }
    const container = $(containerId);
    if (chart) { chart.destroy(); chart = null; }
    container.classList.remove('tile-loading-spinner');
    container.innerHTML = '';
    try {
      await loadApexCharts();
    } catch (e) {
      // ApexCharts genuinely failed to load (offline, ad-blocker, ...) --
      // still just a message in the same slot, not a hidden tile.
      renderMessage("Couldn't load the chart.");
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

    chart = new window.ApexCharts(container, {
      chart: {
        type: 'area',
        // A numeric pixel height, not '100%' -- ApexCharts resolves a
        // percentage height against the parent at mount time, and that
        // measurement came out taller than the container's own height
        // (275px against 200px, observed), which the container (no
        // overflow:hidden) doesn't clip -- the extra height visibly
        // spilled out of the tile and over whatever sat below it. A
        // concrete number matching the CSS height in lobby.css always
        // lands exactly on-target. Keep this in sync with
        // .account-elo-chart's own height there.
        height: 160,
        fontFamily: 'inherit',
        background: 'transparent',
        toolbar: { show: false },
        zoom: { enabled: false },
        animations: { speed: 400 },
      },
      // A legend is pure clutter for a single named series -- it also
      // ate into the height budget above, part of why the chart used to
      // render taller than intended.
      legend: { show: false },
      series: [{ name: 'Rating', data: times.map((t, i) => [t, values[i]]) }],
      colors: [lineColor],
      stroke: { curve: 'smooth', width: 2.5 },
      // The gradient fill under the line is the single biggest visual
      // upgrade a real area chart brings over a bare line. Fading all
      // the way to fully transparent (opacityTo: 0) read as only
      // partially shaded -- the lower portion of the area looked empty.
      // Without an explicit gradientToColors, ApexCharts fades the
      // bottom stop to white rather than to the series' own color, which
      // against this app's light cream panel background read as no fill
      // at all -- so this pins both gradient stops to the same lineColor
      // and only varies the opacity, keeping a visible tinted floor the
      // whole way down instead of washing out to white.
      fill: {
        type: 'gradient',
        gradient: { shadeIntensity: 1, gradientToColors: [lineColor], opacityFrom: 0.4, opacityTo: 0.12, stops: [0, 100] },
      },
      markers: { size: 0, hover: { size: 5 } },
      dataLabels: { enabled: false },
      grid: {
        borderColor: gridColor,
        strokeDashArray: 0,
        xaxis: { lines: { show: false } },
        yaxis: { lines: { show: true } },
        padding: { left: 8, right: 8 },
      },
      xaxis: {
        type: 'datetime',
        labels: { style: { colors: textColor, fontSize: '11px' }, datetimeUTC: false },
        axisBorder: { show: false },
        axisTicks: { show: false },
      },
      yaxis: {
        labels: { style: { colors: textColor, fontSize: '11px' }, formatter: (v) => Math.round(v) },
      },
      tooltip: {
        theme: 'dark',
        x: { format: 'MMM d, yyyy' },
        y: { formatter: (v) => `Rating: ${Math.round(v)}` },
      },
    });
    await chart.render();
  }

  function onRangeClick(e) {
    const btn = e.target.closest('.account-elo-chart-range-btn');
    if (!btn || btn.disabled) return;
    document.querySelectorAll(`#${rangeToggleId} .account-elo-chart-range-btn`).forEach((b) => {
      b.classList.toggle('selected', b === btn);
    });
    render(filterHistoryByRange(fullHistory, btn.dataset.range));
  }

  // Immediately shows `message` in place of the chart, skipping the
  // fetch entirely -- for a case the caller already knows synchronously
  // (a guest profile, before ever asking the server). Disables the
  // range toggle too, since there's nothing to filter.
  function showUnavailable(message) {
    fullHistory = [];
    setRangeToggleEnabled(false);
    renderMessage(message);
  }

  // Fetches `historyUrl` (the caller's own /api/profile/<username>/rating_history)
  // and renders it -- a plain message in the same slot (never hiding
  // anything) if there's genuinely no rated history at all.
  // `emptyMessage` lets each caller phrase that differently (Account:
  // "you" phrasing; Player Profile: third person).
  //
  // `isStillRelevant`, if given, is checked right before touching the DOM
  // -- a Player Profile can navigate from one player to another before
  // this resolves (unlike Account, there's no login/logout gate slowing
  // that down), and without this a slow first fetch could paint stale
  // data into a screen that's since moved on to someone else entirely.
  async function load(historyUrl, { isStillRelevant = () => true, emptyMessage = 'No rated games yet.' } = {}) {
    try {
      const result = await fetchJSON(historyUrl);
      if (!isStillRelevant()) return;
      fullHistory = result.history || [];
      if (fullHistory.length === 0) {
        setRangeToggleEnabled(false);
        renderMessage(emptyMessage);
        return;
      }
      setRangeToggleEnabled(true);
      // A fresh load (a different profile, or just re-opening this
      // screen) always starts from "All" -- a 7D/30D selection left over
      // from a previous visit silently carrying over would show a
      // narrower window than the toggle itself appears to say, with
      // nothing indicating why.
      document.querySelectorAll(`#${rangeToggleId} .account-elo-chart-range-btn`).forEach((b) => {
        b.classList.toggle('selected', b.dataset.range === 'all');
      });
      await render(fullHistory);
    } catch (e) {
      if (isStillRelevant()) { setRangeToggleEnabled(false); renderMessage("Couldn't load Elo history."); }
    }
  }

  return { load, onRangeClick, showUnavailable };
}
