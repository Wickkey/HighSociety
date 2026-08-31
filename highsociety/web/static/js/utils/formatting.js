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

// "2h ago" / "3d ago" -- the Account screen's Recent Activity feed
// (account.js). Falls back to a real calendar date once something's more
// than about a month old, where a relative count stops being more useful
// than just the actual date.
export function timeAgo(isoString) {
  const diffMs = Date.now() - new Date(isoString).getTime();
  const diffSec = Math.max(0, Math.floor(diffMs / 1000));
  if (diffSec < 60) return 'just now';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(isoString).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
