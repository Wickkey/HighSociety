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

function renderGamesList(container, games, myUsername) {
  container.innerHTML = games.map((g) => `
    <button type="button" class="recent-game-row" data-game-id="${g.game_id}">
      <div class="recent-game-row-top">
        <span class="recent-game-date">${new Date(g.finished_at).toLocaleDateString('en-US', DATE_FORMAT)}</span>
        <span class="recent-game-placement${g.placement === 1 ? ' recent-game-placement-first' : ''}">#${g.placement}</span>
      </div>
      <div class="recent-game-opponents">${escapeHtml(opponentsLabel(g, myUsername))}</div>
    </button>
  `).join('');
  container.querySelectorAll('.recent-game-row').forEach((row) => {
    row.addEventListener('click', () => openGameDetailModal(row.dataset.gameId));
  });
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
    const result = await fetchJSON(`/api/games/${encodeURIComponent(profile.username)}`);
    const games = (result.games || []).slice(0, 5);
    // showHomeTile (lobby.js) hides this synchronously the instant a
    // sub-panel (Join/Host/Rules) is picked -- but this fetch is async,
    // so if that happened while it was in flight, this callback landing
    // afterward would otherwise un-hide it again on the wrong screen (a
    // real, reported bug: Recent Games appearing above the Host form).
    // $('home-tiles') being hidden is exactly "no longer on the tile
    // picker", regardless of which sub-panel is now showing instead.
    if (games.length === 0 || $('home-tiles').classList.contains('hidden')) { hide(section); return; }
    renderGamesList($('home-recent-games-list'), games, profile.username);
    show(section);
  } catch (e) {
    hide(section); // transient network hiccup -- just skip the widget
  }
}

// The full "My Games" screen, reached from the Account screen.
export async function showGameHistoryScreen() {
  showScreen('screen-game-history');
  const profile = loadProfile();
  const list = $('game-history-list');
  const empty = $('game-history-empty');
  list.innerHTML = '';
  hide(empty);
  if (!profile) { show(empty); return; }
  try {
    const result = await fetchJSON(`/api/games/${encodeURIComponent(profile.username)}`);
    const games = result.games || [];
    if (games.length === 0) { show(empty); return; }
    renderGamesList(list, games, profile.username);
  } catch (e) {
    show(empty);
  }
}
