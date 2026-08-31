// Tiny DOM helpers used by every other module in this app. showScreen is
// the one exception to "dependency-free" (it needs matchmaking.js's cancel
// hook) -- kept here anyway since every feature module needs to switch
// screens, and this is the one place that already does. Circular import
// with matchmaking.js is safe: only ever touched inside a function body.
import {
  SIDEBAR_HIDDEN_SCREENS, GLOBAL_STATS_FOOTER_SCREENS, SIDEBAR_ACTIVE_BY_PATH, SIDEBAR_ACTIVE_CLEARING_SCREENS,
} from './constants.js';
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
// Highlights the sidebar item for `itemId` (see SIDEBAR_ACTIVE_BY_PATH),
// or clears every item's active state if itemId is falsy -- one place so
// setScreenPath and showScreen's own room-entry clearing (below) both stay
// in sync without either needing to know how the other decided to change.
function setSidebarActive(itemId) {
  document.querySelectorAll('.sidebar-item').forEach((el) => {
    el.classList.toggle('active', el.id === itemId);
  });
}

export function setScreenPath(path) {
  if (location.pathname !== path) history.replaceState(null, '', path);
  // Unconditional, unlike the history write above -- a direct visit/
  // refresh at e.g. /join already has location.pathname === '/join' before
  // this ever runs, so gating this on the same "did it change" check would
  // leave a fresh page load with no sidebar tab highlighted at all.
  // /account/<username> shares its sidebar highlight with the bare
  // /account entry in SIDEBAR_ACTIVE_BY_PATH -- normalize the username
  // segment away before the lookup rather than adding a second entry per
  // possible username.
  const sidebarLookupPath = path.startsWith('/account/') ? '/account' : path;
  setSidebarActive(SIDEBAR_ACTIVE_BY_PATH[sidebarLookupPath] || null);
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
  if (SIDEBAR_ACTIVE_CLEARING_SCREENS.has(id)) setSidebarActive(null);
  // Single central switch for the sticky stats footer, same idea as
  // SIDEBAR_HIDDEN_SCREENS above -- every screen transition goes through
  // here, so no individual screen's show-function needs to know this
  // footer exists. Fetched fresh on every entry rather than cached
  // (cheap query, see loadHomeGlobalStats' own comment); hidden
  // everywhere else.
  if (GLOBAL_STATS_FOOTER_SCREENS.has(id)) loadHomeGlobalStats();
  else hide($('home-global-stats'));
}
