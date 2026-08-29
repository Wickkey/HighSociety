// Player-seat WebSocket lifecycle: fresh join, the IDENTIFY handshake,
// rejoin-token reconnect after a refresh/dropped connection, and the
// rejoin-token bookkeeping that makes reconnect possible next time. Ported
// from the old frontend's network/websocket.js (connectPlayerSocket/
// attemptReconnect) + the player half of network/messages.js
// (handlePlayerMessage/respondIdentify/beginReconnectAttempt).
//
// Deliberately stops interpreting messages once the handshake resolves --
// anything else (auctions, moves, chat, ...) comes back as `phase: 'game'`
// with the raw message attached, for Phase 3's GameContext to take over.
// This hook only owns getting a seat *connected*, not what happens at the
// table once it is.
import { useCallback, useEffect, useRef, useState } from 'react';
import { clearRejoinInfo, loadRejoinInfo, saveRejoinInfo } from '../state/rejoin';
import { connectSocket, type SocketConnection } from '../ws/socket';
import {
  resolveIdentifyAnswer, type IdentifyErrorMessage, type IdentifyMessage, type IdentifySuccessMessage,
  type JoinIdentity, type PlayerSocketMessage,
} from '../ws/protocol';

export type PlayerConnectionState =
  | { phase: 'idle' }
  | { phase: 'connecting' }
  /** Fresh IDENTIFY_SUCCESS -- seated, waiting in the lobby for the table to fill. */
  | { phase: 'waiting' }
  /** IDENTIFY_SUCCESS while resuming an existing seat via a rejoin token. */
  | { phase: 'reconnected' }
  /** A fresh join's IDENTIFY_ERROR (e.g. username already taken at this table). */
  | { phase: 'rejected'; message: string }
  /** A reconnect attempt's IDENTIFY_ERROR -- the token was invalid/expired
   * (resigned, game over, or someone else already reconnected with it). */
  | { phase: 'unavailable'; message: string }
  /** Anything past the handshake -- Phase 3's concern. */
  | { phase: 'game'; message: PlayerSocketMessage };

export interface UsePlayerConnectionResult {
  state: PlayerConnectionState;
  /** Connects fresh and identifies as `identity`. */
  join: (identity: JoinIdentity) => void;
  /** Connects using a stored rejoin token for this room, if one exists.
   * Returns whether an attempt was actually started. */
  attemptReconnectIfPossible: () => boolean;
  disconnect: () => void;
}

/** `onDisconnected` fires whenever the socket closes for any reason (server
 * closed it, network drop) -- NOT when the caller itself calls disconnect().
 * The Room screen uses this to trigger an immediate status re-check rather
 * than waiting for its next poll tick, matching the old app's onclose ->
 * refreshStatus(). */
export function usePlayerConnection(roomCode: string, onDisconnected?: () => void): UsePlayerConnectionResult {
  const [state, setState] = useState<PlayerConnectionState>({ phase: 'idle' });
  const connectionRef = useRef<SocketConnection | null>(null);
  const identityRef = useRef<JoinIdentity | null>(null);
  const reconnectingRef = useRef(false);
  const onDisconnectedRef = useRef(onDisconnected);
  onDisconnectedRef.current = onDisconnected;

  const teardown = useCallback(() => {
    connectionRef.current?.dispose();
    connectionRef.current = null;
  }, []);
  useEffect(() => teardown, [teardown]); // close the socket if the Room screen unmounts/changes rooms

  const open = useCallback((path: string) => {
    teardown();
    setState({ phase: 'connecting' });
    connectionRef.current = connectSocket(path, {
      onMessage: (raw) => {
        // Cast per-case below rather than relying on switch narrowing alone
        // -- GenericGameMessage's necessarily-untyped `message_type: string`
        // (every real in-game message type is Phase 3's concern, not
        // enumerable here) overlaps every literal case, so TS can't narrow
        // out the other variants on its own.
        const msg = raw as PlayerSocketMessage;
        switch (msg.message_type) {
          case 'IDENTIFY': {
            const { prompt } = msg as IdentifyMessage;
            const identity = identityRef.current;
            if (identity) connectionRef.current?.send({ message_type: 'IDENTIFY_ACK', prompt: resolveIdentifyAnswer(prompt, identity) });
            break;
          }
          case 'IDENTIFY_ERROR': {
            const { prompt } = msg as IdentifyErrorMessage;
            if (reconnectingRef.current) {
              reconnectingRef.current = false;
              clearRejoinInfo(roomCode);
              teardown();
              setState({ phase: 'unavailable', message: prompt || 'A game is already in progress. You can watch as a spectator.' });
            } else {
              teardown();
              setState({ phase: 'rejected', message: prompt });
            }
            break;
          }
          case 'IDENTIFY_SUCCESS': {
            const { data } = msg as IdentifySuccessMessage;
            const identity = identityRef.current;
            if (reconnectingRef.current) {
              reconnectingRef.current = false;
              setState({ phase: 'reconnected' });
            } else {
              if (identity && data?.rejoin_token) {
                saveRejoinInfo(roomCode, data.rejoin_token, identity.username, identity.name);
              }
              setState({ phase: 'waiting' });
            }
            break;
          }
          default:
            setState({ phase: 'game', message: msg });
        }
      },
      onClose: () => {
        connectionRef.current = null;
        onDisconnectedRef.current?.();
      },
    });
  }, [roomCode, teardown]);

  const join = useCallback((identity: JoinIdentity) => {
    identityRef.current = identity;
    reconnectingRef.current = false;
    open(`/ws?room=${encodeURIComponent(roomCode)}`);
  }, [roomCode, open]);

  const attemptReconnectIfPossible = useCallback((): boolean => {
    const info = loadRejoinInfo(roomCode);
    if (!info) return false;
    identityRef.current = { username: info.username, name: info.name };
    reconnectingRef.current = true;
    open(`/ws?room=${encodeURIComponent(roomCode)}&rejoin_token=${encodeURIComponent(info.token)}`);
    return true;
  }, [roomCode, open]);

  return { state, join, attemptReconnectIfPossible, disconnect: teardown };
}
