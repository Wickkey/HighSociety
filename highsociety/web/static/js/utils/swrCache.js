// Client-side cache for read-only API data, kept in localStorage so it
// survives a page reload: every screen paints instantly from the last data
// this browser saw, then quietly refreshes from the server
// (stale-while-revalidate). The visible wait on opening Account, a
// profile, the Leaderboard or a past game is the round trip to Supabase --
// with this, that wait only happens the very first time.
//
// Entries expire after MAX_AGE_MS (3 months) and the store is capped at
// MAX_ENTRIES (oldest evicted first), so it can't grow without bound.
// Everything here is public game data (stats, history, leaderboard) --
// nothing private is written to the browser that the server wouldn't hand
// to anyone. Every storage call is wrapped: a full, disabled or private-
// mode localStorage just means no cache, never a broken screen.

const PREFIX = 'hs_cache_v1:';
export const MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000; // 3 months
const MAX_ENTRIES = 200;

export function readCache(key) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (!raw) return null;
    const entry = JSON.parse(raw);
    if (!entry || Date.now() - entry.at > MAX_AGE_MS) {
      localStorage.removeItem(PREFIX + key);
      return null;
    }
    return entry.data;
  } catch (e) {
    return null;
  }
}

export function writeCache(key, data) {
  const value = JSON.stringify({ at: Date.now(), data });
  try {
    localStorage.setItem(PREFIX + key, value);
  } catch (e) {
    // Most likely the quota -- evict the oldest half of our own entries
    // and try once more; give up quietly if that still isn't enough.
    evictOldest(Math.ceil(cacheKeys().length / 2));
    try { localStorage.setItem(PREFIX + key, value); } catch (e2) { return; }
  }
  const keys = cacheKeys();
  if (keys.length > MAX_ENTRIES) evictOldest(keys.length - MAX_ENTRIES);
}

function cacheKeys() {
  const keys = [];
  try {
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (k && k.startsWith(PREFIX)) keys.push(k);
    }
  } catch (e) { /* storage unavailable */ }
  return keys;
}

function evictOldest(count) {
  const aged = cacheKeys().map((k) => {
    let at = 0;
    try { at = JSON.parse(localStorage.getItem(k)).at || 0; } catch (e) { /* corrupt entry -- evict first */ }
    return { k, at };
  }).sort((a, b) => a.at - b.at);
  aged.slice(0, count).forEach(({ k }) => {
    try { localStorage.removeItem(k); } catch (e) { /* ignore */ }
  });
}

// `fetcher` returns a promise of fresh data (e.g. () => fetchJSON(url)).
// Returns {cached, freshPromise}: `cached` is the last stored copy (or
// null), paintable immediately; `freshPromise` resolves to the server's
// current data -- written back to the cache for next time -- or null on
// any failure, so callers can keep whatever they already painted.
// Concurrent callers for the same key share one in-flight request, so a
// prefetch and a screen opening at the same moment cost one round trip.
const inFlight = new Map();

export function swr(key, fetcher) {
  const cached = readCache(key);
  let freshPromise = inFlight.get(key);
  if (!freshPromise) {
    freshPromise = Promise.resolve()
      .then(fetcher)
      .then((data) => { writeCache(key, data); return data; })
      .catch(() => null)
      .finally(() => inFlight.delete(key));
    inFlight.set(key, freshPromise);
  }
  return { cached, freshPromise };
}
