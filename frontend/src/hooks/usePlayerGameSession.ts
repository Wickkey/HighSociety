// Bundles a player's WebSocket connection (usePlayerConnection) with the
// game-state reducer (gameReducer) and the actual player-action senders
// (bid/pass/discard/resign/reactions/chat) into one hook -- PlayerPanel and
// LiveGamePlaceholder both need "own a connection + own a reducer + render
// the right thing," and this is that shared wiring so neither duplicates it.
//
// Includes the "delivery watchdog" ported from the old frontend's
// gameActions.js: a WebSocket can report readyState === OPEN while actually
// being a zombie connection that no longer delivers anything -- send()
// doesn't throw in that case. Without this, a bid/pass/discard would look
// like it worked (panel greyed, local timer cleared) even though the
// server never received it, sitting indistinguishable from "waiting for
// other players" until the per-move timer ran out server-side. Simplified
// from the old app's version: that one only disarmed on three specific
// message types it knew were "real" confirmations; here, literally any
// message reaching onGameMessage already proves the connection is alive
// and moving forward, so anything at all disarms it.
import { useCallback, useReducer, useRef, useState } from 'react';
import { PLAYER_CONNECTED_PHASES, usePlayerConnection, type PlayerConnectionState } from './usePlayerConnection';
import { createInitialGameState, gameReducer } from '../state/gameReducer';
import type { GameState } from '../types/game';
import type { JoinIdentity } from '../ws/protocol';

const ACTION_WATCHDOG_MS = 4000; // comfortably under any real per-move timer

export interface RoomSettings {
  revealCards?: boolean;
  showLogs?: boolean;
  turnTimeLimit?: number | null;
  seed?: number | null;
  manualSeed?: boolean;
}

export interface UsePlayerGameSessionResult {
  connectionState: PlayerConnectionState;
  gameState: GameState;
  isConnected: boolean;
  /** A watchdog-fired or "connection isn't open" message, distinct from
   * gameState.moveError (a genuine server rejection) -- surfaced in the
   * same visual slot by the component, since both mean "your last action
   * needs attention," but they come from different layers and shouldn't be
   * conflated into one piece of state. */
  connectionWarning: string | null;
  join: (identity: JoinIdentity) => void;
  attemptReconnectIfPossible: () => boolean;
  /** Populates the opponents panel from the room's own `joined` roster at
   * connect time -- ported from the old frontend's seedOpponents(), called
   * right alongside join()/attemptReconnectIfPossible() so a player who was
   * already seated before this browser connected shows up immediately
   * rather than only once they take their first action. */
  seedOpponents: (joined: { username: string; name: string }[]) => void;
  actions: {
    selectMoneyCard: (value: number) => void;
    selectDiscardPainting: (value: number) => void;
    placeBid: () => void;
    pass: () => void;
    discardPainting: () => void;
    /** Caller is responsible for confirming first (see useConfirm) -- this
     * just sends once told to. */
    resign: () => void;
    sendChat: (text: string) => void;
    sendReaction: (emoji: string) => void;
    /** The server auto-passes on timeout too, but its broadcast takes a
     * moment to arrive -- marking the panel pending immediately (see
     * hooks/useMoveTimer.ts's onExpire) avoids a stretch where the clock
     * reads 0 but the controls still look live and clickable. */
    expireMoveTimer: () => void;
  };
}

export function usePlayerGameSession(roomCode: string, roomSettings: RoomSettings): UsePlayerGameSessionResult {
  const [gameState, dispatch] = useReducer(gameReducer, null, () => createInitialGameState(null, roomSettings));
  const [connectionWarning, setConnectionWarning] = useState<string | null>(null);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const disarmWatchdog = useCallback(() => {
    if (watchdogRef.current) { clearTimeout(watchdogRef.current); watchdogRef.current = null; }
  }, []);

  const connection = usePlayerConnection(roomCode, {
    onGameMessage: (message) => {
      disarmWatchdog();
      dispatch({ type: 'SERVER_MESSAGE', message });
    },
  });

  const isConnected = PLAYER_CONNECTED_PHASES.has(connection.state.phase);

  const armWatchdog = useCallback(() => {
    disarmWatchdog();
    watchdogRef.current = setTimeout(() => {
      watchdogRef.current = null;
      if (!isConnected) {
        setConnectionWarning('Connection lost — reconnecting…');
        connection.disconnect();
        connection.attemptReconnectIfPossible();
      } else {
        // Still claims open: a genuine zombie connection, not safe to tear
        // down and retry unattended -- surfacing this honestly beats the
        // silent "stuck until the real timer expires" symptom it replaces.
        setConnectionWarning('Still waiting on the server… if this persists, refresh the page.');
      }
    }, ACTION_WATCHDOG_MS);
  }, [connection, disarmWatchdog, isConnected]);

  /** Every player action funnels through this instead of connection.send()
   * directly, so "is the connection actually usable" can't be forgotten at
   * a future call site. Returns whether it was actually sent. */
  const send = useCallback((payload: unknown, { confirmable = true }: { confirmable?: boolean } = {}): boolean => {
    if (!isConnected) {
      setConnectionWarning('Connection lost — reconnecting…');
      connection.disconnect();
      connection.attemptReconnectIfPossible();
      return false;
    }
    setConnectionWarning(null);
    connection.send(payload);
    if (confirmable) armWatchdog();
    return true;
  }, [connection, isConnected, armWatchdog]);

  const join = useCallback((identity: JoinIdentity) => {
    dispatch({ type: 'RESET', myUsername: identity.username, roomSettings });
    connection.join(identity);
  }, [connection, roomSettings]);

  const attemptReconnectIfPossible = useCallback((): boolean => {
    const identity = connection.attemptReconnectIfPossible();
    if (!identity) return false;
    dispatch({ type: 'RESET', myUsername: identity.username, roomSettings });
    return true;
  }, [connection, roomSettings]);

  const seedOpponents = useCallback((joined: { username: string; name: string }[]) => dispatch({ type: 'SEED_OPPONENTS', joined }), []);

  const selectMoneyCard = useCallback((value: number) => dispatch({ type: 'SELECT_MONEY_CARD', value }), []);
  const selectDiscardPainting = useCallback((value: number) => dispatch({ type: 'SELECT_DISCARD_PAINTING', value }), []);

  const placeBid = useCallback(() => {
    if (!gameState.myPrompt || gameState.myPrompt.answered) return;
    if (gameState.selectedBid.length === 0) return; // "select at least one money card" -- caller shows this from gameState.selectedBid being empty, no separate error needed
    if (!send({ message_type: 'RESPONSE', prompt: JSON.stringify(gameState.selectedBid) })) return;
    dispatch({ type: 'BID_SUBMITTED' });
  }, [gameState.myPrompt, gameState.selectedBid, send]);

  const pass = useCallback(() => {
    if (!gameState.myPrompt || gameState.myPrompt.answered) return;
    if (!send({ message_type: 'RESPONSE', prompt: 'pass' })) return;
    dispatch({ type: 'PASS_SUBMITTED' });
  }, [gameState.myPrompt, send]);

  const discardPainting = useCallback(() => {
    if (!gameState.myPrompt || gameState.myPrompt.answered) return;
    if (gameState.selectedDiscardValue === null) return;
    if (!send({ message_type: 'RESPONSE', prompt: String(gameState.selectedDiscardValue) })) return;
    dispatch({ type: 'DISCARD_SUBMITTED' });
  }, [gameState.myPrompt, gameState.selectedDiscardValue, send]);

  const resign = useCallback(() => {
    // Not confirmable (see old gameActions.js's identical note): the server
    // only ever notifies *other* players that someone resigned, never the
    // resigning player themselves, so arming a watchdog here would just be
    // a guaranteed false "still waiting" a few seconds after every
    // successful resign.
    if (!send({ message_type: 'RESIGN' }, { confirmable: false })) return;
    dispatch({ type: 'RESIGNED' });
  }, [send]);

  const sendChat = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed || !isConnected) return;
    connection.send({ message_type: 'CHAT', prompt: trimmed });
    dispatch({ type: 'CHAT_SENT', text: trimmed });
  }, [connection, isConnected]);

  const sendReaction = useCallback((emoji: string) => {
    if (!isConnected) return;
    connection.send({ message_type: 'REACTION', emoji });
  }, [connection, isConnected]);

  const expireMoveTimer = useCallback(() => dispatch({ type: 'MOVE_TIMER_EXPIRED' }), []);

  return {
    connectionState: connection.state,
    gameState,
    isConnected,
    connectionWarning,
    join,
    attemptReconnectIfPossible,
    seedOpponents,
    actions: {
      selectMoneyCard, selectDiscardPainting, placeBid, pass, discardPainting, resign, sendChat, sendReaction, expireMoveTimer,
    },
  };
}
