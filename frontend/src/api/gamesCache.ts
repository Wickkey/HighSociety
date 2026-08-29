// One page of a player's game history, cached in memory keyed by
// `username:offset` -- shared by the Home screen's Recent Games widget
// (which slices the first 5 rows out of this same page-0 fetch) and the
// My Games screen (Phase 4), so opening either one after the other
// paints instantly from what the other already fetched instead of two
// separate requests for overlapping data. Ported from the old frontend's
// gameHistory.js.
import { api } from './client';
import type { RecentGamesPage } from '../types/api';

export const GAMES_PAGE_SIZE = 10;

const cache = new Map<string, RecentGamesPage>();

export function peekGamesPage(username: string, offset: number): RecentGamesPage | null {
  return cache.get(`${username}:${offset}`) ?? null;
}

/** Always fetches fresh (stale-while-revalidate -- pair with peekGamesPage
 * for the instant paint) and updates the shared cache for next time. */
export async function fetchGamesPage(username: string, offset: number): Promise<RecentGamesPage | null> {
  try {
    const page = await api.recentGames(username, GAMES_PAGE_SIZE, offset);
    cache.set(`${username}:${offset}`, page);
    return page;
  } catch {
    return null;
  }
}
