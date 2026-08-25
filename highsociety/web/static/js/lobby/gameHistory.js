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

function opponentsLabel(game, myUsername) {
  const others = game.opponents.filter((o) => o.name !== myUsername);
  if (others.length === 0) return 'solo';
  return others.map((o) => `${o.name}${o.is_bot ? ' (bot)' : ''}`).join(', ');
}

function renderGamesList(container, games, myUsername) {
  container.innerHTML = games.map((g) => `
    <button type="button" class="recent-game-row" data-game-id="${g.game_id}">
      <span class="recent-game-date">${new Date(g.finished_at).toLocaleDateString()}</span>
      <span class="recent-game-opponents">vs ${escapeHtml(opponentsLabel(g, myUsername))}</span>
      <span class="recent-game-placement">#${g.placement}</span>
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
    if (games.length === 0) { hide(section); return; }
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
