// Tiny DOM helpers used by every other module in this app. showScreen is
// the one exception to "dependency-free" (it needs matchmaking.js's cancel
// hook) -- kept here anyway since every feature module needs to switch
// screens, and this is the one place that already does. Circular import
// with matchmaking.js is safe: only ever touched inside a function body.
import { SIDEBAR_HIDDEN_SCREENS } from './constants.js';
import { cancelMatchmakingTicketQuietly } from '../lobby/matchmaking.js';

export const $ = (id) => document.getElementById(id);

export function hide(el) { el.classList.add('hidden'); }
export function show(el) { el.classList.remove('hidden'); }
export function showError(el, text) { el.textContent = text; el.classList.remove('hidden'); }

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
}
