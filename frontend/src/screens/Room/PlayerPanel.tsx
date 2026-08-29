// The "join this room as a player" form, and (once connected) handing off
// to the waiting room / the live game screen. Ported from the old
// frontend's lobby.js (onJoin, applyJoinIdentityDefaults/
// onChangeJoinIdentity) + network/messages.js's player IDENTIFY_SUCCESS/
// IDENTIFY_ERROR handling, which usePlayerConnection.ts (via
// usePlayerGameSession) already owns -- this component just renders
// whatever phase that hook reports.
import { useEffect, useRef, useState } from 'react';
import { PLAYER_CONNECTED_PHASES } from '../../hooks/usePlayerConnection';
import { usePlayerGameSession } from '../../hooks/usePlayerGameSession';
import { useProfile } from '../../state/ProfileContext';
import { useRoom } from '../../state/RoomContext';
import type { RoomStatus } from '../../types/api';
import { GameScreen } from '../Game/GameScreen';
import { roomSettingsFromStatus } from './roomSettings';
import { WaitingRoom } from './WaitingRoom';
import styles from './Room.module.css';

export function PlayerPanel({
  roomCode, status, onWatchInstead, autoJoin = false,
}: { roomCode: string; status: RoomStatus; onWatchInstead: () => void; autoJoin?: boolean }) {
  const { profile, saveProfile } = useProfile();
  const { setConnectionRole } = useRoom();
  const session = usePlayerGameSession(roomCode, roomSettingsFromStatus(status));
  const [editingIdentity, setEditingIdentity] = useState(!profile);
  const [username, setUsername] = useState(profile?.username ?? '');
  const statusRef = useRef(status);
  statusRef.current = status;

  useEffect(() => {
    setConnectionRole(PLAYER_CONNECTED_PHASES.has(session.connectionState.phase) ? 'player' : 'none');
  }, [session.connectionState.phase, setConnectionRole]);

  // Landed here straight from a matched matchmaking ticket -- that already
  // *is* the user's opt-in to join, so connect immediately with no extra
  // click, matching the old app's enterJustMatchedRoom -> onJoin().
  // Deliberately no "already tried" ref guard -- see
  // LiveGamePlaceholder.tsx's identical comment on why that would break
  // under React 18 StrictMode's dev-only double-invoke of effects.
  useEffect(() => {
    if (autoJoin && profile) {
      session.join({ username: profile.username, name: profile.username });
      session.seedOpponents(statusRef.current.joined ?? []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoJoin, profile]);

  // Still genuinely waiting for the table to fill, specifically -- once a
  // real game message arrives (phase 'game') or a rejoin resumes an
  // already-started game ('reconnected'), that's no longer true even if
  // this tab never saw the room's own status flip past 'lobby'.
  if (session.connectionState.phase === 'waiting' && status.state === 'lobby') {
    return <WaitingRoom roomCode={roomCode} status={status} />;
  }
  if (PLAYER_CONNECTED_PHASES.has(session.connectionState.phase)) return <GameScreen session={session} />;

  function onJoin() {
    const name = username.trim();
    if (!name) return;
    saveProfile(name, name); // this device's identity going forward -- see ProfileContext
    session.join({ username: name, name });
    session.seedOpponents(status.joined ?? []);
  }

  const connecting = session.connectionState.phase === 'connecting';

  return (
    <div className="card panel">
      <h2>Join this game</h2>
      {profile && !editingIdentity ? (
        <p>
          Joining as <strong>{username}</strong> —{' '}
          <button type="button" className={styles.linkButton} onClick={() => setEditingIdentity(true)}>not you?</button>
        </p>
      ) : (
        <label>
          Username
          <input
            type="text" value={username} autoComplete="off"
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
      )}
      <button type="button" className="primary" onClick={onJoin} disabled={connecting || !username.trim()}>
        {connecting ? 'Joining…' : 'Join'}
      </button>
      {session.connectionState.phase === 'rejected' && <p className="error">{session.connectionState.message}</p>}
      <p className={styles.hint}>
        <button type="button" className={styles.linkButton} onClick={onWatchInstead}>Watch as a spectator instead</button>
      </p>
    </div>
  );
}
