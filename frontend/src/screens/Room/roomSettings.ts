// Derives the fixed-for-the-whole-table settings a fresh GameState needs
// (see gameReducer.ts's createInitialGameState) from a room's /api/status
// response -- shared by PlayerPanel, LiveGamePlaceholder, and
// SpectatorPanel so there's exactly one place that knows the wire field
// names (snake_case, as the API sends them) map to.
import type { RoomSettings } from '../../hooks/usePlayerGameSession';
import type { RoomStatus } from '../../types/api';

export function roomSettingsFromStatus(status: RoomStatus): RoomSettings {
  return {
    revealCards: status.reveal_cards !== false,
    showLogs: status.show_logs !== false,
    turnTimeLimit: (status.turn_time_limit as number | null | undefined) ?? null,
    seed: (status.seed as number | null | undefined) ?? null,
    manualSeed: !!status.manual_seed,
  };
}
