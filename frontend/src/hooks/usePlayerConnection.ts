// Player-seat WebSocket lifecycle: fresh join, the IDENTIFY handshake,
// rejoin-token reconnect after a refresh/dropped connection, and the
// rejoin-token bookkeeping that makes reconnect possible next time. Ported
// from the old frontend's network/websocket.js (connectPlayerSocket/
// attemptReconnect) + the player half of network/messages.js
// (handlePlayerMessage/respondIdentify/beginReconnectAttempt).
//
// Deliberately stops interpreting messages once the handshake resolves --
// anything else (auctions, moves, chat, ...) is handed to `onGameMessage`
// (Phase 3's GameContext reducer) via a direct callback, NOT folded into
// this hook's own `state`. That matters: React 18 batches state updates
// scheduled from outside React's own event system (a WebSocket's onmessage
// included), so if several messages arrived close together and each just
// called setState(latestMessage), React could coalesce two `setState`
// calls into one render and silently drop the earlier message before
// anything ever saw it -- a real correctness risk for a card game where
// every auction event must be applied in order. A reducer's `dispatch`
// doesn't have this problem (React guarantees the reducer runs once per
// dispatched action, in order, regardless of how the resulting renders get
// batched), so the message stream is handed to one via a plain callback
// invoked synchronously from the socket handler, never through this hook's
// own state.
import { useCallback, useEffect, useRef, useState } from 'react';
import { clearRejoinInfo, loadRejoinInfo, saveRejoinInfo } from '../state/rejoin';
import { connectSocket, type SocketConnection } from '../ws/socket';
import {
  resolveIdentifyAnswer, type GenericGameMessage, type IdentifyErrorMessage, type IdentifyMessage, type IdentifySuccessMessage,
  type JoinIdentity, type PlayerSocketMessage, type RematchMessage,
} from '../ws/protocol';

/** Any phase past a successful identify -- the connection is live and
 * (once past 'waiting', which only applies pre-game-start) receiving real
 * game messages. Shared by every component that needs to tell "connected"
 * apart from "still working on it"/"failed", so that definition lives in
 * exactly one place. */
export const PLAYER_CONNECTED_PHASES = new Set(['waiting', 'reconnected', 'game']);

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
  /** Past the handshake, receiving real messages -- see `onGameMessage`
   * for the messages themselves; this is purely a one-time UI transition
   * ("stop showing a waiting/reconnecting stub, Phase 3's screen takes
   * over now"), not updated again per message. */
  | { phase: 'game' };

export interface UsePlayerConnectionOptions {
  /** Fires whenever the socket closes for any reason (server closed it,
   * network drop) -- NOT when the caller itself calls disconnect(). The
   * Room screen uses this to trigger an immediate status re-check rather
   * than waiting for its next poll tick, matching the old app's onclose ->
   * refreshStatus(). */
  onDisconnected?: () => void;
  /** Called synchronously, once per message, for every message after the
   * IDENTIFY handshake resolves (i.e. never for IDENTIFY/IDENTIFY_ERROR/
   * IDENTIFY_SUCCESS themselves, which this hook already fully owns) --
   * see this module's own top comment for why that's a callback and not
   * part of `state`. */
  onGameMessage?: (message: GenericGameMessage | RematchMessage) => void;
}

export interface UsePlayerConnectionResult {
  state: PlayerConnectionState;
  /** Connects fresh and identifies as `identity`. */
  join: (identity: JoinIdentity) => void;
  /** Connects using a stored rejoin token for this room, if one exists.
   * Returns the identity it's reconnecting as (so a caller resetting its
   * own game state -- see usePlayerGameSession.ts -- knows whose seat this
   * is), or null if there was no token to try. */
  attemptReconnectIfPossible: () => JoinIdentity | null;
  /** Sends a player action (bid/pass/discard/resign/reaction) once
   * connected -- a no-op if there's no live socket, since every real
   * caller (gameReducer's action-sending side) already checks `state.phase`
   * first and has its own "connection lost" handling for that case. */
  send: (data: unknown) => void;
  disconnect: () => void;
}

export function usePlayerConnection(roomCode: string, options: UsePlayerConnectionOptions = {}): UsePlayerConnectionResult {
  const [state, setState] = useState<PlayerConnectionState>({ phase: 'idle' });
  const connectionRef = useRef<SocketConnection | null>(null);
  const identityRef = useRef<JoinIdentity | null>(null);
  const reconnectingRef = useRef(false);
  const optionsRef = useRef(options);
  optionsRef.current = options;

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
            // Functional update bails out of a re-render entirely once
            // already 'game' (returning the same `prev` reference is
            // React's own signal to skip it) -- after the very first
            // in-game message, this line becomes a no-op and every
            // subsequent message flows purely through onGameMessage below.
            setState((prev) => (prev.phase === 'game' ? prev : { phase: 'game' }));
            optionsRef.current.onGameMessage?.(msg as GenericGameMessage | RematchMessage);
        }
      },
      onClose: () => {
        connectionRef.current = null;
        optionsRef.current.onDisconnected?.();
      },
    });
  }, [roomCode, teardown]);

  const join = useCallback((identity: JoinIdentity) => {
    identityRef.current = identity;
    reconnectingRef.current = false;
    open(`/ws?room=${encodeURIComponent(roomCode)}`);
  }, [roomCode, open]);

  const attemptReconnectIfPossible = useCallback((): JoinIdentity | null => {
    const info = loadRejoinInfo(roomCode);
    if (!info) return null;
    const identity: JoinIdentity = { username: info.username, name: info.name };
    identityRef.current = identity;
    reconnectingRef.current = true;
    open(`/ws?room=${encodeURIComponent(roomCode)}&rejoin_token=${encodeURIComponent(info.token)}`);
    return identity;
  }, [roomCode, open]);

  const send = useCallback((data: unknown) => {
    connectionRef.current?.send(data);
  }, []);

  return { state, join, attemptReconnectIfPossible, send, disconnect: teardown };
}
