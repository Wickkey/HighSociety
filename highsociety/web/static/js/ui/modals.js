// Generic confirm dialog + the public profile-viewer modal.
import { $, hide, show } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { fetchJSON } from '../lobby/lobby.js';

// In-page confirm dialog (resign, leaving mid-game — see gameActions.js's
// onResign/lobby.js's onHomeLinkClick) styled like every other panel in the
// app, replacing the browser's own native confirm() popup. Promise-based so
// a call site just reads `if (!(await confirmDialog(...))) return;`, same
// shape as the native confirm() it replaces. Only one can be open at a time
// in this app (both call sites are themselves mutually exclusive — you
// can't resign and click the home link in the same instant), so a single
// pending resolver is enough; no stacking/queueing needed.
let _confirmDialogResolve = null;
export function confirmDialog(message, confirmLabel) {
  return new Promise((resolve) => {
    _confirmDialogResolve = resolve;
    $('confirm-modal-message').textContent = message;
    $('confirm-modal-confirm').textContent = confirmLabel;
    show($('confirm-modal'));
  });
}
export function resolveConfirmDialog(result) {
  hide($('confirm-modal'));
  const resolve = _confirmDialogResolve;
  _confirmDialogResolve = null;
  if (resolve) resolve(result);
}

// Public profile viewer -- games played / win rate / Elo are visible for
// any player (unlike achievements, which stay on your own Account screen
// only), reachable today by clicking a name on the finished-game standings
// table (see lobby/rematch.js's renderFinished). Clicking a bot's name
// works too, it'll just come back 404 (bots have no players row) and show
// the "no games recorded" state, which is a fine, honest outcome.
export async function openProfileModal(username) {
  $('profile-view-username').textContent = username;
  $('profile-view-avatar').textContent = username.charAt(0).toUpperCase();
  hide($('profile-view-elo-line'));
  hide($('profile-view-stats-row'));
  hide($('profile-view-empty'));
  show($('profile-view-modal'));
  try {
    const stats = await fetchJSON(`/api/profile/${encodeURIComponent(username)}`);
    $('profile-view-elo').textContent = stats.elo;
    show($('profile-view-elo-line'));
    $('profile-view-games').textContent = stats.games_played;
    $('profile-view-winrate').textContent = `${Math.round(stats.win_rate * 100)}%`;
    show($('profile-view-stats-row'));
  } catch (e) {
    show($('profile-view-empty'));
  }
}
export function closeProfileModal() { hide($('profile-view-modal')); }

// "Click a game -> see full results" -- the same modal serves both the
// My Games screen and the home page's Recent Games widget (see
// lobby/gameHistory.js, which only ever fetches the list; this owns the
// one detail view both lists open into).
export async function openGameDetailModal(gameId) {
  const body = $('game-detail-body');
  body.innerHTML = '';
  $('game-detail-date').textContent = '';
  show($('game-detail-modal'));
  try {
    const detail = await fetchJSON(`/api/games/detail/${encodeURIComponent(gameId)}`);
    $('game-detail-date').textContent = new Date(detail.finished_at).toLocaleDateString();
    body.innerHTML = detail.participants.map((p) => `
      <tr>
        <td>${p.placement != null ? p.placement : '—'}</td>
        <td>${escapeHtml(p.name)}${p.is_bot ? ' (bot)' : ''}${p.is_winner ? ' 🏆' : ''}</td>
        <td>${p.points}</td>
        <td>${p.money_left}</td>
      </tr>
    `).join('');
  } catch (e) {
    body.innerHTML = '<tr><td colspan="4">Could not load this game.</td></tr>';
  }
}
export function closeGameDetailModal() { hide($('game-detail-modal')); }
