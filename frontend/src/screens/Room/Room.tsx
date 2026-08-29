// Route entry for /room/:code -- validates the room exists, then renders
// whichever sub-view matches its current state. Ported from the old
// frontend's lobby.js enterRoom + renderForStatus's own screen-switch.
import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useRoom } from '../../state/RoomContext';
import { Lobby } from './Lobby';
import { LiveGamePlaceholder } from './LiveGamePlaceholder';
import styles from './Room.module.css';

export function Room() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  // Set by Matchmaking.tsx's navigate(..., { state: { autoJoin: true } })
  // right after a match/bot-fill -- see PlayerPanel's own comment on why
  // that's treated as an implicit "join" click.
  const autoJoin = !!(location.state as { autoJoin?: boolean } | null)?.autoJoin;
  const { roomCode, status, enterRoom, leaveRoom } = useRoom();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!code) return undefined;
    setError(null);
    enterRoom(code).catch((e) => setError((e as Error).message));
    return () => leaveRoom();
  }, [code, enterRoom, leaveRoom]);

  if (error) {
    return (
      <div className={styles.roomWrap}>
        <div className="card panel">
          <p className="error">{error}</p>
          <button type="button" className="secondary" onClick={() => navigate('/')}>Back to Home</button>
        </div>
      </div>
    );
  }

  // Also true for one tick right after a room-code change, before the new
  // room's own first status has landed -- avoids flashing the *previous*
  // room's Lobby/LiveGamePlaceholder under the new code.
  if (!status || roomCode !== code) {
    return (
      <div className={styles.roomWrap}>
        <div className="card panel"><p className="muted">Loading room…</p></div>
      </div>
    );
  }

  if (status.state === 'finished') {
    return (
      <div className={styles.roomWrap}>
        <div className="card panel">
          <h2>Game over</h2>
          <p className="muted">The results screen (standings, rematch, Elo reveal) lands in Phase 4 -- for now, this just confirms the game finished.</p>
          <button type="button" className="primary" onClick={() => navigate('/')}>Return to Home</button>
        </div>
      </div>
    );
  }

  if (status.state === 'lobby') return <Lobby roomCode={code!} status={status} autoJoin={autoJoin} />;
  return <LiveGamePlaceholder roomCode={code!} />;
}
