// Generic text-formatting helpers with no dependency on game/app state.

// Usernames are entirely user-supplied and end up interpolated into a couple
// of innerHTML strings (final standings, the turn indicator) — escape them
// first so a username like "<img src=x onerror=...>" renders as inert text
// instead of executing in every other connected browser.
const _escapeHtmlEl = document.createElement('div');
export function escapeHtml(text) {
  _escapeHtmlEl.textContent = text;
  return _escapeHtmlEl.innerHTML;
}

export function ordinal(n) {
  const rem10 = n % 10;
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  return `${n}${{ 1: 'st', 2: 'nd', 3: 'rd' }[rem10] || 'th'}`;
}
