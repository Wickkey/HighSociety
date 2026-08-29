// The post-join "you're in, waiting for the table to fill" view -- ported
// from the old frontend's playerList.js (join-waiting section + onAddBot).
// Seat roster comes straight from RoomContext's own status poll rather than
// a second independent poll -- see RoomContext.tsx's comment on why the old
// app's separate startWaitingRoomPolling isn't needed here.
import { useState } from 'react';
import { api } from '../../api/client';
import type { RoomStatus } from '../../types/api';
import styles from './Room.module.css';

const BOT_TYPES = ['easy', 'medium', 'hard'] as const;

export function WaitingRoom({ roomCode, status }: { roomCode: string; status: RoomStatus }) {
  const [botType, setBotType] = useState<string>('medium');
  const [error, setError] = useState('');

  const joined = status.joined ?? [];
  const seats = status.seats ?? joined.length;
  const names = joined.length ? joined.map((p) => `${p.name}${p.is_bot ? ' 🤖' : ''}`).join(', ') : 'nobody yet';

  async function onAddBot() {
    setError('');
    try {
      await api.addBot(roomCode, botType);
      // The status poll (already running via RoomContext) picks up the new
      // seat count on its own next tick; if this bot filled the last seat,
      // the game starts server-side and status.state moves past 'lobby'.
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="card panel">
      <h2>You&apos;re in!</h2>
      <p className="muted">Seats filled: {joined.length}/{seats} ({names})</p>
      {joined.length < seats && (
        <div className={styles.addBotRow}>
          <select value={botType} onChange={(e) => setBotType(e.target.value)}>
            {BOT_TYPES.map((t) => <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
          </select>
          <button type="button" className="secondary" onClick={onAddBot}>Add bot</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <p className={styles.hint}>Waiting for the table to fill — the game starts automatically once every seat is taken.</p>
    </div>
  );
}
