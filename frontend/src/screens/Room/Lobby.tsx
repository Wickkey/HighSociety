// The room's "lobby" state -- room code/link always visible, plus either
// the join form or the spectate form depending on which this tab picked.
// Which connection is actually live (and thus which of PlayerPanel/
// SpectatorPanel's own sub-views render) is each panel's own concern; this
// component only owns the join-vs-spectate choice itself.
import { useState } from 'react';
import type { RoomStatus } from '../../types/api';
import { PlayerPanel } from './PlayerPanel';
import { RoomCodeCard } from './RoomCodeCard';
import { SpectatorPanel } from './SpectatorPanel';
import styles from './Room.module.css';

export function Lobby({ roomCode, status, autoJoin = false }: { roomCode: string; status: RoomStatus; autoJoin?: boolean }) {
  const [mode, setMode] = useState<'join' | 'spectate'>('join');

  return (
    <div className={styles.roomWrap}>
      <RoomCodeCard status={status} />
      {mode === 'join'
        ? <PlayerPanel roomCode={roomCode} status={status} autoJoin={autoJoin} onWatchInstead={() => setMode('spectate')} />
        : <SpectatorPanel roomCode={roomCode} status={status} onBack={() => setMode('join')} />}
    </div>
  );
}
