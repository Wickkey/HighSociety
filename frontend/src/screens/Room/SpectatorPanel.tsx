// The "watch this room as a spectator" form, and (once connected) the
// live spectator view -- the read-only counterpart to PlayerPanel. Ported
// from the old frontend's lobby.js (onSpectateJoin/
// applySpectateIdentityDefaults).
import { useEffect, useState } from 'react';
import { useSpectatorGameSession } from '../../hooks/useSpectatorGameSession';
import { useProfile } from '../../state/ProfileContext';
import { useRoom } from '../../state/RoomContext';
import type { RoomStatus } from '../../types/api';
import { SpectateScreen } from '../Game/SpectateScreen';
import { roomSettingsFromStatus } from './roomSettings';
import styles from './Room.module.css';

// See PlayerPanel's identical CONNECTED_PHASES comment -- real game
// messages ('game') can start arriving the instant IDENTIFY_SUCCESS
// resolves, well before this component would otherwise call it settled.
const CONNECTED_PHASES = new Set(['connected', 'game']);

export function SpectatorPanel({
  roomCode, status, onBack,
}: { roomCode: string; status: RoomStatus; onBack?: () => void }) {
  const { profile } = useProfile();
  const { setConnectionRole } = useRoom();
  const session = useSpectatorGameSession(roomCode, roomSettingsFromStatus(status));
  const [editingIdentity, setEditingIdentity] = useState(!profile);
  const [username, setUsername] = useState(profile?.username ?? '');

  useEffect(() => {
    setConnectionRole(CONNECTED_PHASES.has(session.connectionState.phase) ? 'spectator' : 'none');
  }, [session.connectionState.phase, setConnectionRole]);

  if (CONNECTED_PHASES.has(session.connectionState.phase)) return <SpectateScreen session={session} />;

  function onJoin() {
    const name = username.trim();
    if (!name) return;
    session.join({ username: name, name });
    session.seedOpponents(status.joined ?? []);
  }

  const connecting = session.connectionState.phase === 'connecting';

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
      {session.connectionState.phase === 'rejected' && <p className="error">{session.connectionState.message}</p>}
      {onBack && (
        <p className={styles.hint}>
          <button type="button" className={styles.linkButton} onClick={onBack}>Back to joining instead</button>
        </p>
      )}
    </div>
  );
}
