// Tiny DOM helpers used by every other module in this app. showScreen is
// the one exception to "dependency-free" (it needs matchmaking.js's cancel
// hook) -- kept here anyway since every feature module needs to switch
// screens, and this is the one place that already does. Circular import
// with matchmaking.js is safe: only ever touched inside a function body.
import { SIDEBAR_HIDDEN_SCREENS, GLOBAL_STATS_FOOTER_SCREENS } from './constants.js';
import { cancelMatchmakingTicketQuietly } from '../lobby/matchmaking.js';
import { loadHomeGlobalStats } from '../lobby/lobby.js';

export const $ = (id) => document.getElementById(id);

export function hide(el) { el.classList.add('hidden'); }
export function show(el) { el.classList.remove('hidden'); }
export function showError(el, text) { el.textContent = text; el.classList.remove('hidden'); }

// Gives the 7 static top-level screens (Play/Join/Host/Leaderboard/Rules/
// Account/Achievements) a real, shareable/refreshable URL -- matching
// Flask routes in web_server.py all serve the same index.html, and
// proceedPastLogin (login.js) reads location.pathname on boot to land
// straight on the right one. replaceState, not pushState: this is about
// the URL bar reflecting *what's currently shown* for sharing/refresh,
// not a full back/forward history of internal screen transitions (room-
// specific navigation already manages its own history entries via
// ?room=<code>, untouched by this).
export function setScreenPath(path) {
  if (location.pathname !== path) history.replaceState(null, '', path);
}

export function showScreen(id) {
  // See lobby/matchmaking.js's cancelMatchmakingTicketQuietly own comment --
  // catches every way off this screen that isn't the Cancel/Add-bots
  // buttons themselves.
  if (id !== 'screen-matchmaking' && !$('screen-matchmaking').classList.contains('hidden')) {
    cancelMatchmakingTicketQuietly();
  }
  document.querySelectorAll('.screen').forEach((s) => s.classList.add('hidden'));
  $(id).classList.remove('hidden');
  // The profile chip has nothing meaningful to show yet on the login screen
  // itself (no profile exists before it's gotten past) -- a "Username"
  // placeholder chip sitting in the header there just looks like stray,
  // half-set-up state rather than an intentional part of the screen.
  $('profile-chip-wrap').classList.toggle('hidden', id === 'screen-login');
  document.querySelector('.app-shell').classList.toggle('sidebar-hidden', SIDEBAR_HIDDEN_SCREENS.has(id));
  // Single central switch for the sticky stats footer, same idea as
  // SIDEBAR_HIDDEN_SCREENS above -- every screen transition goes through
  // here, so no individual screen's show-function needs to know this
  // footer exists. Fetched fresh on every entry rather than cached
  // (cheap query, see loadHomeGlobalStats' own comment); hidden
  // everywhere else.
  if (GLOBAL_STATS_FOOTER_SCREENS.has(id)) loadHomeGlobalStats();
  else hide($('home-global-stats'));
}
