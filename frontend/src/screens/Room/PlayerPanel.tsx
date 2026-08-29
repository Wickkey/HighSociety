// The "join this room as a player" form, and (once connected) handing off
// to the waiting room / a Phase-3 stub. Ported from the old frontend's
// lobby.js (onJoin, applyJoinIdentityDefaults/onChangeJoinIdentity) +
// network/messages.js's player IDENTIFY_SUCCESS/IDENTIFY_ERROR handling,
// which usePlayerConnection.ts already owns -- this component just renders
// whatever phase that hook reports.
import { useEffect, useState } from 'react';
import { usePlayerConnection } from '../../hooks/usePlayerConnection';
import { useProfile } from '../../state/ProfileContext';
import { useRoom } from '../../state/RoomContext';
import type { RoomStatus } from '../../types/api';
import { ConnectedStub } from './ConnectedStub';
import { WaitingRoom } from './WaitingRoom';
import styles from './Room.module.css';

// Any phase past a successful identify -- see ConnectedStub's own comment.
// Notably includes 'game': the table can start (and real in-game messages
// start arriving on this same socket) well before RoomContext's own status
// poll notices and swaps this screen away, so a message landing here doesn't
// mean anything went wrong.
const CONNECTED_PHASES = new Set(['waiting', 'reconnected', 'game']);

export function PlayerPanel({
  roomCode, status, onWatchInstead, autoJoin = false,
}: { roomCode: string; status: RoomStatus; onWatchInstead: () => void; autoJoin?: boolean }) {
  const { profile, saveProfile } = useProfile();
  const { setConnectionRole } = useRoom();
  const player = usePlayerConnection(roomCode);
  const [editingIdentity, setEditingIdentity] = useState(!profile);
  const [username, setUsername] = useState(profile?.username ?? '');

  useEffect(() => {
    setConnectionRole(CONNECTED_PHASES.has(player.state.phase) ? 'player' : 'none');
  }, [player.state.phase, setConnectionRole]);

  // Landed here straight from a matched matchmaking ticket -- that already
  // *is* the user's opt-in to join, so connect immediately with no extra
  // click, matching the old app's enterJustMatchedRoom -> onJoin().
  // Deliberately no "already tried" ref guard -- see
  // LiveGamePlaceholder.tsx's identical comment on why that would break
  // under React 18 StrictMode's dev-only double-invoke of effects.
  // player.join()'s own open() always disposes any previous connection
  // before starting fresh, so re-running this is a safe no-op-if-redundant.
  useEffect(() => {
    if (autoJoin && profile) {
      player.join({ username: profile.username, name: profile.username });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoJoin, profile]);

  // Still genuinely waiting for the table to fill, specifically -- once a
  // real game message arrives (phase 'game') or a rejoin resumes an
  // already-started game ('reconnected'), that's no longer true even if
  // this tab never saw the room's own status flip past 'lobby'.
  if (player.state.phase === 'waiting' && status.state === 'lobby') {
    return <WaitingRoom roomCode={roomCode} status={status} />;
  }
  if (CONNECTED_PHASES.has(player.state.phase)) return <ConnectedStub />;

  function onJoin() {
    const name = username.trim();
    if (!name) return;
    saveProfile(name, name); // this device's identity going forward -- see ProfileContext
    player.join({ username: name, name });
  }

  const connecting = player.state.phase === 'connecting';

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
      {player.state.phase === 'rejected' && <p className="error">{player.state.message}</p>}
      <p className={styles.hint}>
        <button type="button" className={styles.linkButton} onClick={onWatchInstead}>Watch as a spectator instead</button>
      </p>
    </div>
  );
}
