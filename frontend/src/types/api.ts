// Response/request shapes for every /api/* endpoint the app talks to.
// Kept as one file for now (mirrors how the old frontend's lobby.js owned
// the one fetchJSON helper every screen shared) -- splits naturally once a
// screen's own types file wants to own its slice.

export interface AppConfig {
  gaMeasurementId: string | null;
  googleClientId: string | null;
}

export interface GoogleAuthResult {
  username?: string;
  display_name?: string;
  needs_username?: boolean;
  suggested_display_name?: string;
}

export interface RoomSummary {
  room_code: string;
  seats: number;
  human_seats: number;
  joined: number;
  bot_mix: string[];
}

export interface GlobalStats {
  total_games: number;
  total_players: number;
}

export interface AccountStats {
  username: string;
  games_played: number;
  wins: number;
  win_rate: number;
  avg_placement: number | null;
  avg_points: number | null;
  avg_money_remaining: number | null;
  elo: number | string;
}

export interface RecentGameOpponent {
  name: string;
  is_bot: boolean;
  is_winner: boolean;
}

export interface RecentGame {
  game_id: number;
  finished_at: string;
  placement: number;
  opponents: RecentGameOpponent[];
}

export interface RecentGamesPage {
  games: RecentGame[];
  has_more: boolean;
}

export interface CreateGameRequest {
  seats: number;
  bot_mix: string[];
  bot_think_time: number;
  visibility: 'public' | 'private';
  turn_time_limit: number | null;
  reveal_cards: boolean;
  show_logs: boolean;
  host_username: string | null;
  seed?: number;
}

export interface RoomStatus {
  room_code: string;
  exists: boolean;
  state: string;
  [key: string]: unknown; // the full shape grows a lot once the lobby/game screens (Phase 2-3) need it -- not worth typing exhaustively yet
}
