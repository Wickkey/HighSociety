// The spectator counterpart to usePlayerGameSession.ts -- no player
// actions or delivery watchdog (nothing here is time-limited or needs
// confirmed delivery the way a bid does), just the connection + reducer
// wiring plus chat.
import { useCallback, useReducer } from 'react';
import { useSpectatorConnection, type SpectatorConnectionState } from './useSpectatorConnection';
import { createInitialGameState, gameReducer } from '../state/gameReducer';
import type { GameState } from '../types/game';
import type { JoinIdentity } from '../ws/protocol';
import type { RoomSettings } from './usePlayerGameSession';

export interface UseSpectatorGameSessionResult {
  connectionState: SpectatorConnectionState;
  gameState: GameState;
  join: (identity: JoinIdentity) => void;
  seedOpponents: (joined: { username: string; name: string }[]) => void;
  /** target 'spectators' reaches only other spectators; 'all' (the
   * default) reaches everyone, players included -- mirrors the old
   * frontend's spec-chat-target-toggle. */
  sendChat: (text: string, target?: 'all' | 'spectators') => void;
}

export function useSpectatorGameSession(roomCode: string, roomSettings: RoomSettings): UseSpectatorGameSessionResult {
  const [gameState, dispatch] = useReducer(gameReducer, null, () => createInitialGameState(null, roomSettings));

  const connection = useSpectatorConnection(roomCode, {
    onGameMessage: (message) => dispatch({ type: 'SERVER_MESSAGE', message }),
  });

  const join = useCallback((identity: JoinIdentity) => {
    dispatch({ type: 'RESET', myUsername: null, roomSettings });
    connection.join(identity);
  }, [connection, roomSettings]);

  const sendChat = useCallback((text: string, target: 'all' | 'spectators' = 'all') => {
    const trimmed = text.trim();
    if (!trimmed) return;
    connection.send({ message_type: 'CHAT', prompt: trimmed, target });
    dispatch({ type: 'CHAT_SENT', text: trimmed });
  }, [connection]);

  const seedOpponents = useCallback((joined: { username: string; name: string }[]) => dispatch({ type: 'SEED_OPPONENTS', joined }), []);

  return {
    connectionState: connection.state, gameState, join, seedOpponents, sendChat,
  };
}
