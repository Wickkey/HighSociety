// Rendered once a room's status moves past 'lobby' (starting/in_progress)
// -- ported from the old frontend's lobby.js renderForStatus's own
// starting/in_progress branch: try a stored rejoin token first (a refresh
// or dropped connection mid-game should silently resume the same seat),
// falling back to "already in progress, watch as a spectator" when there's
// no token or it's no longer valid. The live board itself is Phase 3 --
// both outcomes here are stubs confirming the *connection* succeeded.
import { useEffect, useState } from 'react';
import { usePlayerConnection } from '../../hooks/usePlayerConnection';
import { useRoom } from '../../state/RoomContext';
import { ConnectedStub } from './ConnectedStub';
import { SpectatorPanel } from './SpectatorPanel';
import styles from './Room.module.css';

export function LiveGamePlaceholder({ roomCode }: { roomCode: string }) {
  const { setConnectionRole } = useRoom();
  const player = usePlayerConnection(roomCode);
  const [hadToken, setHadToken] = useState<boolean | null>(null);

  // Deliberately unconditional (no "have we already tried" ref guard) --
  // usePlayerConnection's own open() always tears down any previous
  // connection before starting a new one, so re-running this is a safe,
  // idempotent "connect from scratch". That matters because React 18
  // StrictMode's dev-only double-invoke of effects (mount -> cleanup ->
  // mount again, to catch exactly this class of bug) would otherwise kill
  // the one connection attempt a ref guard allowed and then refuse to make
  // a second one, leaving this screen stuck on "Reconnecting..." forever in
  // dev. Production builds run this effect exactly once, as intended.
  useEffect(() => {
    setHadToken(player.attemptReconnectIfPossible());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomCode]);

  // 'game' counts as connected here too -- see PlayerPanel's identical
  // CONNECTED_PHASES comment: real in-game messages can start arriving on
  // this socket the instant the reconnect handshake succeeds, well before
  // this component would otherwise call it "reconnected".
  useEffect(() => {
    setConnectionRole(player.state.phase === 'reconnected' || player.state.phase === 'game' ? 'player' : 'none');
  }, [player.state.phase, setConnectionRole]);

  if (hadToken === null) return null;

  if (hadToken && (player.state.phase === 'idle' || player.state.phase === 'connecting')) {
    return (
      <div className={styles.roomWrap}>
        <div className="card panel"><p className="muted">Reconnecting…</p></div>
      </div>
    );
  }

  if (player.state.phase === 'reconnected' || player.state.phase === 'game') {
    return <div className={styles.roomWrap}><ConnectedStub /></div>;
  }

  // No token existed at all, or the reconnect attempt was rejected
  // (phase 'unavailable') -- either way, fall back to offering to spectate.
  const message = player.state.phase === 'unavailable'
    ? player.state.message
    : 'A game is already in progress. You can watch as a spectator.';

  return (
    <div className={styles.roomWrap}>
      <div className="card panel"><p>{message}</p></div>
      <SpectatorPanel roomCode={roomCode} />
    </div>
  );
}
