// Generic confirm dialog + the public profile-viewer modal.
import { $, hide, show } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { fetchJSON } from '../lobby/lobby.js';
// Circular with gameHistory.js (which imports openGameDetailModal from
// here) via playerProfile.js's own import of fetchGamesPage/renderIfChanged
// from gameHistory.js -- safe by this project's own established
// convention (see gameHistory.js's identical note on this exact pair):
// every side of the cycle only ever touches another side's export inside
// a function body (onGameDetailTableClick below; wireRowClicks in
// gameHistory.js), never at any module's own top-level evaluation.
import { showPlayerProfileScreen } from '../lobby/playerProfile.js';

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

// "Click a game -> see full results" -- the same modal serves the Home
// widget, My Games, Account's Recent Activity, and a Player Profile's own
// game history (see lobby/gameHistory.js, which only ever fetches the
// list; this owns the one detail view every caller opens into).
export async function openGameDetailModal(gameId) {
  // Captured the instant the modal opens (before anything else changes)
  // -- this is a real screen id (e.g. 'screen-host-setup' for Home,
  // 'screen-game-history' for My Games, 'screen-account'), since the
  // modal is only ever an overlay on top of whatever's already showing.
  // Used by onGameDetailTableClick below so a name clicked inside this
  // modal returns Back to wherever the modal was actually opened from --
  // a real reported bug otherwise: it always assumed Leaderboard,
  // landing there even when the modal was opened from Home.
  gameDetailOpenerScreenId = document.querySelector('.screen:not(.hidden)')?.id || null;
  const body = $('game-detail-body');
  body.innerHTML = '';
  $('game-detail-date').textContent = '';
  show($('game-detail-modal'));
  try {
    const detail = await fetchJSON(`/api/games/detail/${encodeURIComponent(gameId)}`);
    $('game-detail-date').textContent = new Date(detail.finished_at).toLocaleDateString();
    body.innerHTML = detail.participants.map((p) => {
      // A human's own name is always a real, linkable username. A bot's
      // displayed name is a per-seat flavor label ("Ziggy bot") that
      // never had a profile of its own -- bot_profile_username (see
      // get_game_detail's own docstring) is the shared difficulty
      // identity's real username to link to instead, when this game
      // actually recorded one (older games might not have).
      const linkUsername = p.is_bot ? p.bot_profile_username : p.name;
      const nameHtml = linkUsername
        ? `<button type="button" class="name-link" data-username="${escapeHtml(linkUsername)}">${escapeHtml(p.name)}</button>`
        : escapeHtml(p.name);
      return `
      <tr>
        <td>${p.placement != null ? p.placement : '—'}</td>
        <td>${nameHtml}${p.is_bot ? ' (bot)' : ''}${p.is_winner ? ' 🏆' : ''}</td>
        <td>${p.points}</td>
        <td>${p.money_left}</td>
      </tr>
    `;
    }).join('');
  } catch (e) {
    body.innerHTML = '<tr><td colspan="4">Could not load this game.</td></tr>';
  }
}
export function closeGameDetailModal() { hide($('game-detail-modal')); }

let gameDetailOpenerScreenId = null; // see openGameDetailModal's own comment

// Wired from index.html's game-detail-body click delegation (see app.js,
// same data-attribute-click-delegation pattern rematch.js's own
// onStandingsTableClick already uses for its own name links). Closing
// the modal first, then navigating, matches what closing it always did
// anyway -- this just also happens to land somewhere instead of nowhere.
// The opener screen id is passed straight through as returnTo -- app.js's
// Back handler is what turns a screen id into "how do I re-show that
// screen" (it already owns every one of those re-show functions).
// Falls back to 'leaderboard' for the one case that's genuinely ambiguous
// (this same modal opened from *another* Player Profile's own game
// history -- chasing that arbitrarily deep would need a real navigation
// stack, not worth it for how rarely that specific path comes up).
export function onGameDetailTableClick(e) {
  const btn = e.target.closest('[data-username]');
  if (btn) {
    const returnTo = (gameDetailOpenerScreenId && gameDetailOpenerScreenId !== 'screen-player-profile')
      ? gameDetailOpenerScreenId
      : 'leaderboard';
    closeGameDetailModal();
    showPlayerProfileScreen(btn.dataset.username, returnTo);
  }
}
