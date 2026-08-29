// One fetch wrapper every endpoint call goes through -- mirrors the old
// frontend's lobby.js fetchJSON exactly (including surfacing the
// backend's own {"error": "..."} message on a non-2xx response, which
// every screen's error-display logic depends on).
import type {
  AccountStats, AppConfig, CreateGameRequest, GlobalStats, GoogleAuthResult, RecentGamesPage, RoomStatus, RoomSummary,
} from '../types/api';

export async function fetchJSON<T>(url: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body as T;
}

function postJSON<T>(url: string, payload: unknown): Promise<T> {
  return fetchJSON<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export const api = {
  config: () => fetchJSON<AppConfig>('/api/app_config'),

  authGoogle: (idToken: string) => postJSON<GoogleAuthResult>('/api/auth/google', { id_token: idToken }),
  authGoogleClaimUsername: (idToken: string, username: string, displayName: string) =>
    postJSON<GoogleAuthResult>('/api/auth/google/claim_username', {
      id_token: idToken, username, display_name: displayName,
    }),
  authGuestSuggest: () => fetchJSON<{ username: string }>('/api/auth/guest/suggest'),
  authGuestClaim: (username: string) => postJSON<{ username: string }>('/api/auth/guest/claim', { username }),
  authUsernameChange: (oldUsername: string, newUsername: string) =>
    postJSON<{ username: string }>('/api/auth/username/change', { old_username: oldUsername, new_username: newUsername }),

  rooms: () => fetchJSON<{ rooms: RoomSummary[] }>('/api/rooms'),
  createGame: (body: CreateGameRequest) => postJSON<RoomStatus>('/api/create_game', body),
  status: (roomCode: string) => fetchJSON<RoomStatus>(`/api/status?room=${encodeURIComponent(roomCode)}`),
  globalStats: async (): Promise<GlobalStats | null> => {
    const res = await fetch('/api/global_stats');
    if (res.status === 204) return null;
    return res.json();
  },

  accountStats: (username: string) => fetchJSON<AccountStats>(`/api/profile/${encodeURIComponent(username)}`),
  recentGames: (username: string, limit: number, offset: number) =>
    fetchJSON<RecentGamesPage>(`/api/games/${encodeURIComponent(username)}?limit=${limit}&offset=${offset}`),
  achievements: (username: string) =>
    fetchJSON<{ achievements: string[] }>(`/api/achievements?username=${encodeURIComponent(username)}`),
};
