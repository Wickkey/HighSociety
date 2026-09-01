// Recent-games list rendering -- shared by the home screen's small Recent
// Games widget, the full My Games screen, and Account's Recent Activity
// section (see account.js's loadRecentActivity). All three fetch
// /api/games/<username> (or share a cached response via fetchGamesPage)
// and paint the exact same row markup via gamesListHtml/renderIfChanged --
// deliberately one shared look, not three screens each free to drift into
// their own; the actual "click a game -> see full results" view lives in
// ui/modals.js's openGameDetailModal, which every caller reuses without
// knowing anything about the list that led to it.
import { $, hide, show, showScreen, setScreenPath } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { loadProfile } from '../auth/profile.js';
import { fetchJSON } from './lobby.js';
import { openGameDetailModal } from '../ui/modals.js';
// Circular with account.js (which imports fetchGamesPage from here) --
// safe by this project's own established convention (see lobby.js/
// gameState.js/websocket.js's identical notes): both sides only ever
// touch the other's export inside a function body (loadHomeRecentGames
// below; loadRecentActivity in account.js), never at this module's own
// top-level evaluation, so load order never matters.
import { getPrefetchedStats } from '../account/account.js';

// Bot display names already end in "bot" (see ai/bot_names.py's naming
// convention -- "Ziggy bot", "Milo bot") -- appending "(bot)" again read
// as a literal double "bot bot" and was the main source of this list
// looking unpolished.
function opponentsLabel(game, myUsername) {
  const others = game.opponents.filter((o) => o.name !== myUsername);
  if (others.length === 0) return 'Solo game';
  return others.map((o) => o.name).join(', ');
}

const DATE_FORMAT = { month: 'short', day: 'numeric', year: 'numeric' };

// Medal-colored for a top-3 finish (gold/silver/bronze), same treatment a
// leaderboard rank gets -- every other placement just uses the neutral
// pill it always has, since there's nothing distinct about finishing 4th
// vs 5th to call out.
function placementClass(placement) {
  if (placement === 1) return ' recent-game-placement-1';
  if (placement === 2) return ' recent-game-placement-2';
  if (placement === 3) return ' recent-game-placement-3';
  return '';
}

// null (never a fake 0) for any game this player wasn't rated in -- a
// guest account, an all-bot practice game, or a game predating the
// ratings table (see get_recent_games' own docstring) -- rendered as no
// badge at all on that row, not a misleading "+0".
function ratingDeltaHtml(game) {
  if (game.rating_change == null) return '';
  const sign = game.rating_change > 0 ? '+' : '';
  const cls = game.rating_change > 0 ? 'positive' : game.rating_change < 0 ? 'negative' : '';
  return `<span class="recent-game-elo-delta ${cls}">${sign}${game.rating_change} Elo</span>`;
}

// The one row template every list of a player's games renders through --
// the Home screen's Recent Games widget, the full My Games screen, and
// Account's Recent Activity feed all call this same function (directly or
// via renderIfChanged below) rather than each keeping its own copy, so
// they can't visually drift apart the way Account's own bespoke version
// once did.
function gamesListHtml(games, myUsername) {
  return games.map((g) => `
    <button type="button" class="recent-game-row" data-game-id="${g.game_id}">
      <span class="recent-game-placement${placementClass(g.placement)}">#${g.placement}</span>
      <span class="recent-game-body">
        <span class="recent-game-opponents">${escapeHtml(opponentsLabel(g, myUsername))}</span>
        <span class="recent-game-date">${new Date(g.finished_at).toLocaleDateString('en-US', DATE_FORMAT)}</span>
      </span>
      ${ratingDeltaHtml(g)}
      <svg class="recent-game-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>
    </button>
  `).join('');
}

function wireRowClicks(container) {
  container.querySelectorAll('.recent-game-row:not([data-wired])').forEach((row) => {
    row.dataset.wired = 'true'; // re-rendering a page whose rows are already wired shouldn't double-bind them
    row.addEventListener('click', () => openGameDetailModal(row.dataset.gameId));
  });
}

// Renders `games` into `container` -- but only actually touches the DOM
// if the resulting HTML differs from what's already there. Home, My
// Games, and Account's Recent Activity all call this on every visit
// (stale-while-revalidate, see fetchGamesPage below), and re-painting
// identical content every single time was the whole "annoying tiny
// refresh" report: nothing visibly changed, yet the list still flashed.
// Skipping a no-op DOM write makes the common case (nothing changed since
// last look) produce zero visible change, matching what actually
// happened. Exported so Account's own Recent Activity section paints
// through this exact same function -- see this module's own top comment.
export function renderIfChanged(container, games, myUsername) {
  const html = gamesListHtml(games, myUsername);
  if (container.dataset.renderedHtml === html) return false;
  container.innerHTML = html;
  container.dataset.renderedHtml = html;
  wireRowClicks(container);
  return true;
}

// One page of a player's game history, cached in memory keyed by
// `username:offset` -- deliberately NOT keyed by limit: the home widget
// only ever wants the first 5 rows of offset 0, and My Games wants 10,
// so both fetch the same PAGE_SIZE=10 superset for offset 0 and the home
// widget just slices what it needs from it. That's the literal fix for
// "My Games is loading slowly, if it's already cached on the home page
// why not display it" -- they're now the same cache entry, not two
// separate fetches for overlapping data.
const PAGE_SIZE = 10;
const gamesPageCache = new Map(); // key -> {games, has_more}

// Returns {cached, freshPromise}: `cached` is last known good data for
// this page (or null the very first time), paintable immediately with no
// wait; `freshPromise` resolves to the real current data (or null on a
// transient failure) and also updates the cache for next time -- classic
// stale-while-revalidate, same pattern as account.js's own stats
// prefetch. Exported so account.js's Recent Activity feed can share this
// exact cache (not a separate fetch of its own) -- whichever of Home/
// Account happens to load first warms it for the other, so opening
// Account right after browsing Home (the overwhelmingly common order,
// since Home is the app's own landing screen) costs zero extra network
// wait for this data.
export function fetchGamesPage(username, offset) {
  const key = `${username}:${offset}`;
  const cached = gamesPageCache.get(key) || null;
  const freshPromise = fetchJSON(`/api/games/${encodeURIComponent(username)}?limit=${PAGE_SIZE}&offset=${offset}`)
    .then((result) => {
      const page = { games: result.games || [], has_more: !!result.has_more };
      gamesPageCache.set(key, page);
      return page;
    })
    .catch(() => null);
  return { cached, freshPromise };
}

// Home screen widget -- top 5 games for whoever's currently signed in,
// hidden entirely for a fresh profile with nothing to show yet (see
// index.html's #home-recent-games, hidden by default for the same
// reason #home-global-stats is).
export async function loadHomeRecentGames() {
  const profile = loadProfile();
  const section = $('home-recent-games');
  if (!profile) { hide(section); return; }
  const { cached, freshPromise } = fetchGamesPage(profile.username, 0);
  if (cached) paintHomeRecentGames(cached, profile.username, section);
  const fresh = await freshPromise;
  if (fresh) paintHomeRecentGames(fresh, profile.username, section);
  else if (!cached) hide(section); // no cache and the fetch failed -- nothing to show
  loadHomeEloBadge(profile);
}

// The current Elo rating, shown right on Home next to "Recent Games" --
// not just buried on the Account screen. Reuses account.js's own
// login-time stats prefetch (see getPrefetchedStats) rather than firing a
// second network request for the same data this widget doesn't otherwise
// need. Hidden for a guest, same gate Account's own Elo hero uses (a
// guest's elo column never actually moves).
async function loadHomeEloBadge(profile) {
  const badge = $('home-recent-games-elo');
  if (!profile || !profile.google_id) { hide(badge); return; }
  try {
    const stats = await getPrefetchedStats(profile.username);
    if (!stats) { hide(badge); return; }
    $('home-recent-games-elo-value').textContent = stats.elo;
    show(badge);
  } catch (e) {
    hide(badge);
  }
}

function paintHomeRecentGames(page, username, section) {
  const games = page.games.slice(0, 5);
  // showHomeTile (lobby.js) hides #home-grid synchronously the instant a
  // sub-panel (Join/Host/Rules) is picked -- but this can resolve after
  // that happened, so it would otherwise un-hide it again on the wrong
  // screen (a real, reported bug: Recent Games appearing above the Host
  // form). $('home-grid') being hidden is exactly "no longer on the
  // tile picker", regardless of which sub-panel is now showing instead
  // (checking #home-tiles itself here used to work the same way, back
  // when showHomeTile hid that element directly -- it now hides the
  // whole #home-grid wrapper instead, see that function's own comment
  // on why).
  if (games.length === 0 || $('home-grid').classList.contains('hidden')) { hide(section); return; }
  const changed = renderIfChanged($('home-recent-games-list'), games, username);
  const alreadyShown = !section.classList.contains('hidden');
  if (changed || !alreadyShown) showEnter(section);
}

// Same pop-in vocabulary used elsewhere (finished-trophy, elo-reveal) --
// removing 'hidden' alone made this widget's very first appearance each
// page load (or the trip back from Host/Join/Rules to the tile picker)
// pop into place instantly with no transition at all, which read as
// abrupt next to everything else on this screen animating in. See
// game.css's .home-recent-games.enter for the actual fade/rise. Only
// actually called when the section wasn't already showing this exact
// content -- see paintHomeRecentGames.
function showEnter(el) {
  el.classList.remove('hidden', 'enter');
  void el.offsetWidth; // force reflow so the class removal above actually takes effect first
  el.classList.add('enter');
}

// The full "My Games" screen -- real pagination (10 per page, Prev/Next)
// rather than a "Load more" button that only ever grows, matching the
// Leaderboard's own pagination.
let gameHistoryOffset = 0;
let gameHistoryUsername = null;
// This screen now has three separate entry points -- the sidebar/a
// direct /my-games link, Home's own "Game History" widget, and Account's
// -- so its own Back button can no longer just hardcode "go to Home"
// (a real reported bug: opening it from Account and pressing Back landed
// on Home instead of back on Account). `returnTo` records which one was
// actually used, read by app.js's onGameHistoryBackClick.
let gameHistoryReturnTo = 'home';

export function getGameHistoryReturnTo() {
  return gameHistoryReturnTo;
}

export function showGameHistoryScreen(returnTo = 'home') {
  gameHistoryReturnTo = returnTo;
  showScreen('screen-game-history');
  setScreenPath('/my-games');
  const profile = loadProfile();
  gameHistoryOffset = 0;
  gameHistoryUsername = profile ? profile.username : null;
  if (!profile) {
    $('game-history-list').innerHTML = '';
    hide($('game-history-pagination'));
    show($('game-history-empty'));
    return;
  }
  loadGameHistoryPage();
}

export function onGameHistoryPrevClick() {
  gameHistoryOffset = Math.max(0, gameHistoryOffset - PAGE_SIZE);
  loadGameHistoryPage();
}

export function onGameHistoryNextClick() {
  gameHistoryOffset += PAGE_SIZE;
  loadGameHistoryPage();
}

async function loadGameHistoryPage() {
  const username = gameHistoryUsername;
  const offset = gameHistoryOffset;
  const list = $('game-history-list');
  const { cached, freshPromise } = fetchGamesPage(username, offset);
  if (cached) {
    paintGameHistoryPage(cached, username, offset);
  } else {
    hide($('game-history-empty'));
    hide($('game-history-pagination'));
    list.innerHTML = '<p class="muted">Loading…</p>';
    delete list.dataset.renderedHtml; // the loading message above isn't a real page render -- don't let the next real one think it's a no-op match
  }
  const fresh = await freshPromise;
  if (username !== gameHistoryUsername || offset !== gameHistoryOffset) return; // navigated away or changed page while this was in flight
  if (fresh) {
    paintGameHistoryPage(fresh, username, offset);
  } else if (!cached) {
    list.innerHTML = '';
    show($('game-history-empty'));
  }
}

function paintGameHistoryPage(page, username, offset) {
  const list = $('game-history-list');
  const empty = $('game-history-empty');
  const pagination = $('game-history-pagination');
  if (page.games.length === 0) {
    if (offset === 0) {
      list.innerHTML = '';
      delete list.dataset.renderedHtml;
      show(empty);
      hide(pagination);
      return;
    }
    // Landed past the real end (e.g. the list shrank between visits) --
    // fall back a page instead of stranding the visitor on a blank one.
    gameHistoryOffset = Math.max(0, offset - PAGE_SIZE);
    loadGameHistoryPage();
    return;
  }
  hide(empty);
  renderIfChanged(list, page.games, username);
  $('btn-game-history-prev').disabled = offset === 0;
  $('btn-game-history-next').disabled = !page.has_more;
  $('game-history-page-label').textContent = `${offset + 1}–${offset + page.games.length}`;
  show(pagination);
}
