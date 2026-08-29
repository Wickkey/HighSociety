// Spectator WebSocket lifecycle -- the read-only counterpart to
// usePlayerConnection.ts. Ported from the old frontend's
// network/websocket.js (connectSpectatorSocket) + the spectator half of
// network/messages.js (handleSpectatorMessage). No rejoin-token handling:
// spectating has no seat to resume, you just watch again from wherever the
// game currently is.
//
// See usePlayerConnection's identical top comment for why in-game messages
// go through `onGameMessage` (a plain callback) rather than this hook's own
// `state` -- same batching-loss risk, same fix.
import { useCallback, useEffect, useRef, useState } from 'react';
import { connectSocket, type SocketConnection } from '../ws/socket';
import {
  resolveIdentifyAnswer, type GenericGameMessage, type IdentifyErrorMessage, type IdentifyMessage,
  type JoinIdentity, type SpectatorSocketMessage,
} from '../ws/protocol';

export type SpectatorConnectionState =
  | { phase: 'idle' }
  | { phase: 'connecting' }
  | { phase: 'connected' }
  | { phase: 'rejected'; message: string }
  | { phase: 'game' };

export interface UseSpectatorConnectionOptions {
  /** See usePlayerConnection's identical option. */
  onDisconnected?: () => void;
  /** See usePlayerConnection's identical option -- never fires for
   * IDENTIFY/IDENTIFY_ERROR/IDENTIFY_SUCCESS, which this hook already
   * fully owns. */
  onGameMessage?: (message: GenericGameMessage) => void;
}

export interface UseSpectatorConnectionResult {
  state: SpectatorConnectionState;
  join: (identity: JoinIdentity) => void;
  /** Sends a message once connected (chat) -- a no-op with no live socket. */
  send: (data: unknown) => void;
  disconnect: () => void;
}

export function useSpectatorConnection(roomCode: string, options: UseSpectatorConnectionOptions = {}): UseSpectatorConnectionResult {
  const [state, setState] = useState<SpectatorConnectionState>({ phase: 'idle' });
  const connectionRef = useRef<SocketConnection | null>(null);
  const identityRef = useRef<JoinIdentity | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const teardown = useCallback(() => {
    connectionRef.current?.dispose();
    connectionRef.current = null;
  }, []);
  useEffect(() => teardown, [teardown]);

  const join = useCallback((identity: JoinIdentity) => {
    identityRef.current = identity;
    teardown();
    setState({ phase: 'connecting' });
    connectionRef.current = connectSocket(`/ws_spectate?room=${encodeURIComponent(roomCode)}`, {
      onMessage: (raw) => {
        // See usePlayerConnection's identical comment on why each case
        // re-casts rather than relying on switch narrowing alone.
        const msg = raw as SpectatorSocketMessage;
        switch (msg.message_type) {
          case 'IDENTIFY': {
            const { prompt } = msg as IdentifyMessage;
            const identity = identityRef.current;
            if (identity) connectionRef.current?.send({ message_type: 'IDENTIFY_ACK', prompt: resolveIdentifyAnswer(prompt, identity) });
            break;
          }
          case 'IDENTIFY_ERROR':
            teardown();
            setState({ phase: 'rejected', message: (msg as IdentifyErrorMessage).prompt });
            break;
          case 'IDENTIFY_SUCCESS':
            setState({ phase: 'connected' });
            break;
          default:
            setState((prev) => (prev.phase === 'game' ? prev : { phase: 'game' }));
            optionsRef.current.onGameMessage?.(msg as GenericGameMessage);
        }
      },
      onClose: () => {
        connectionRef.current = null;
        optionsRef.current.onDisconnected?.();
      },
    });
  }, [roomCode, teardown]);

  const send = useCallback((data: unknown) => {
    connectionRef.current?.send(data);
  }, []);

  return { state, join, send, disconnect: teardown };
}
