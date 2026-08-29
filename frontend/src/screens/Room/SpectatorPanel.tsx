// The "watch this room as a spectator" form, and (once connected) a
// Phase-3 stub -- the read-only counterpart to PlayerPanel. Ported from the
// old frontend's lobby.js (onSpectateJoin/applySpectateIdentityDefaults).
import { useEffect, useState } from 'react';
import { useSpectatorConnection } from '../../hooks/useSpectatorConnection';
import { useProfile } from '../../state/ProfileContext';
import { useRoom } from '../../state/RoomContext';
import styles from './Room.module.css';

// See PlayerPanel's identical CONNECTED_PHASES comment -- real game
// messages ('game') can start arriving the instant IDENTIFY_SUCCESS
// resolves, well before this component would otherwise call it settled.
const CONNECTED_PHASES = new Set(['connected', 'game']);

export function SpectatorPanel({ roomCode, onBack }: { roomCode: string; onBack?: () => void }) {
  const { profile } = useProfile();
  const { setConnectionRole } = useRoom();
  const spectator = useSpectatorConnection(roomCode);
  const [editingIdentity, setEditingIdentity] = useState(!profile);
  const [username, setUsername] = useState(profile?.username ?? '');

  useEffect(() => {
    setConnectionRole(CONNECTED_PHASES.has(spectator.state.phase) ? 'spectator' : 'none');
  }, [spectator.state.phase, setConnectionRole]);

  if (CONNECTED_PHASES.has(spectator.state.phase)) {
    return (
      <div className="card panel">
        <h2>Spectating room {roomCode}</h2>
        <p className="muted">The live board lands in Phase 3 -- for now, this just confirms the spectator connection works.</p>
      </div>
    );
  }

  function onJoin() {
    const name = username.trim();
    if (!name) return;
    spectator.join({ username: name, name });
  }

  const connecting = spectator.state.phase === 'connecting';

  return (
    <div className="card panel">
      <h2>Watch as a spectator</h2>
      {profile && !editingIdentity ? (
        <p>
          Watching as <strong>{username}</strong> —{' '}
          <button type="button" className={styles.linkButton} onClick={() => setEditingIdentity(true)}>not you?</button>
        </p>
      ) : (
        <label>
          Username
          <input type="text" value={username} autoComplete="off" onChange={(e) => setUsername(e.target.value)} />
        </label>
      )}
      <button type="button" className="primary" onClick={onJoin} disabled={connecting || !username.trim()}>
        {connecting ? 'Connecting…' : 'Watch'}
      </button>
      {spectator.state.phase === 'rejected' && <p className="error">{spectator.state.message}</p>}
      {onBack && (
        <p className={styles.hint}>
          <button type="button" className={styles.linkButton} onClick={onBack}>Back to joining instead</button>
        </p>
      )}
    </div>
  );
}
