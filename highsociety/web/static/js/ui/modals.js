// Generic confirm dialog + the public profile-viewer modal.
import { $, hide, show } from '../utils/dom.js';
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
