// Recent-games list rendering -- shared by the home screen's small Recent
// Games widget and the full My Games screen (see account.js's
// showAccountScreen, which links here via #btn-account-my-games). Both
// just fetch /api/games/<username> and hand the same rows to
// renderGamesList; the actual "click a game -> see full results" view
// lives in ui/modals.js's openGameDetailModal, which either caller can
// reuse without knowing anything about the list that led to it.
import { $, hide, show, showScreen } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { loadProfile } from '../auth/profile.js';
import { fetchJSON } from './lobby.js';
import { openGameDetailModal } from '../ui/modals.js';

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

function gamesListHtml(games, myUsername) {
  return games.map((g) => `
    <button type="button" class="recent-game-row" data-game-id="${g.game_id}">
      <div class="recent-game-row-top">
        <span class="recent-game-date">${new Date(g.finished_at).toLocaleDateString('en-US', DATE_FORMAT)}</span>
        <span class="recent-game-placement${g.placement === 1 ? ' recent-game-placement-first' : ''}">#${g.placement}</span>
      </div>
      <div class="recent-game-opponents">${escapeHtml(opponentsLabel(g, myUsername))}</div>
    </button>
  `).join('');
}

function wireRowClicks(container) {
  container.querySelectorAll('.recent-game-row:not([data-wired])').forEach((row) => {
    row.dataset.wired = 'true'; // appendGamesList calls this repeatedly across pages -- don't double-bind rows already wired by an earlier page
    row.addEventListener('click', () => openGameDetailModal(row.dataset.gameId));
  });
}

// Home widget: always a full, one-shot render (it never paginates).
function renderGamesList(container, games, myUsername) {
  container.innerHTML = gamesListHtml(games, myUsername);
  wireRowClicks(container);
}

// My Games: appends a page's rows onto whatever's already there instead
// of replacing it, so "Load more" grows the list rather than restarting
// it from scratch.
function appendGamesList(container, games, myUsername) {
  container.insertAdjacentHTML('beforeend', gamesListHtml(games, myUsername));
  wireRowClicks(container);
}

// Home screen widget -- top few games for whoever's currently signed in,
// hidden entirely for a fresh profile with nothing to show yet (see
// index.html's #home-recent-games, hidden by default for the same
// reason #home-global-stats is).
export async function loadHomeRecentGames() {
  const profile = loadProfile();
  const section = $('home-recent-games');
  if (!profile) { hide(section); return; }
  try {
    const result = await fetchJSON(`/api/games/${encodeURIComponent(profile.username)}?limit=5`);
    const games = result.games || [];
    // showHomeTile (lobby.js) hides this synchronously the instant a
    // sub-panel (Join/Host/Rules) is picked -- but this fetch is async,
    // so if that happened while it was in flight, this callback landing
    // afterward would otherwise un-hide it again on the wrong screen (a
    // real, reported bug: Recent Games appearing above the Host form).
    // $('home-tiles') being hidden is exactly "no longer on the tile
    // picker", regardless of which sub-panel is now showing instead.
    if (games.length === 0 || $('home-tiles').classList.contains('hidden')) { hide(section); return; }
    renderGamesList($('home-recent-games-list'), games, profile.username);
    showEnter(section);
  } catch (e) {
    hide(section); // transient network hiccup -- just skip the widget
  }
}

// Same pop-in vocabulary used elsewhere (finished-trophy, elo-reveal) --
// removing 'hidden' alone made this widget's very first appearance each
// page load (or the trip back from Host/Join/Rules to the tile picker)
// pop into place instantly with no transition at all, which read as
// abrupt next to everything else on this screen animating in. See
// game.css's .home-recent-games.enter for the actual fade/rise.
function showEnter(el) {
  el.classList.remove('hidden', 'enter');
  void el.offsetWidth; // force reflow so the class removal above actually takes effect first
  el.classList.add('enter');
}

// The full "My Games" screen, reached from the Account screen -- 10 games
// per page (see FRONTEND_FIXES.MD: the home widget's top-5 stays a single
// fast fetch, but loading a whole history in one shot here was the part
// that felt slow) via a "Load more" button appending the next page rather
// than a full prev/next pager, since there's no need to go back to an
// earlier page once its rows are already on screen.
const PAGE_SIZE = 10;
let gameHistoryOffset = 0;
let gameHistoryUsername = null;

export async function showGameHistoryScreen() {
  showScreen('screen-game-history');
  const profile = loadProfile();
  const list = $('game-history-list');
  const empty = $('game-history-empty');
  const loadMoreBtn = $('btn-game-history-load-more');
  list.innerHTML = '';
  hide(empty);
  hide(loadMoreBtn);
  gameHistoryOffset = 0;
  gameHistoryUsername = profile ? profile.username : null;
  if (!profile) { show(empty); return; }
  await loadNextGameHistoryPage();
}

async function loadNextGameHistoryPage() {
  const username = gameHistoryUsername;
  const list = $('game-history-list');
  const empty = $('game-history-empty');
  const loadMoreBtn = $('btn-game-history-load-more');
  try {
    const result = await fetchJSON(
      `/api/games/${encodeURIComponent(username)}?limit=${PAGE_SIZE}&offset=${gameHistoryOffset}`,
    );
    if (username !== gameHistoryUsername) return; // screen navigated away/reopened while this was in flight
    const games = result.games || [];
    if (gameHistoryOffset === 0 && games.length === 0) { show(empty); hide(loadMoreBtn); return; }
    appendGamesList(list, games, username);
    gameHistoryOffset += games.length;
    loadMoreBtn.classList.toggle('hidden', !result.has_more);
  } catch (e) {
    if (gameHistoryOffset === 0) show(empty);
  }
}

// Wired from app.js's static handlers.
export function onLoadMoreGameHistory() {
  loadNextGameHistoryPage();
}
