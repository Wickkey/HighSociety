// The /play route -- Elo matchmaking setup + waiting room. Ported from the
// old frontend's lobby/matchmaking.js; the ticket lifecycle itself lives in
// hooks/useMatchmaking.ts, this just renders whatever phase it reports.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMatchmaking } from '../hooks/useMatchmaking';
import { useProfile } from '../state/ProfileContext';
import styles from './Matchmaking.module.css';

export function Matchmaking() {
  const { profile } = useProfile();
  const navigate = useNavigate();
  const { state, findMatch, cancel, fillWithBots } = useMatchmaking();
  const [seats, setSeats] = useState(3);

  if (state.phase === 'matched') {
    // Same "no extra click" contract as a manual room-code join once a
    // match is confirmed -- see PlayerPanel's autoJoin handling.
    navigate(`/room/${encodeURIComponent(state.roomCode)}`, { replace: true, state: { autoJoin: true } });
    return null;
  }

  if (state.phase === 'idle' || state.phase === 'error') {
    return (
      <div className={styles.wrap}>
        <div className="card panel">
          <h2>Find a match</h2>
          <label>
            Number of players
            <input type="number" min={2} max={5} value={seats} onChange={(e) => setSeats(Number(e.target.value))} />
          </label>
          <button
            type="button" className="primary"
            onClick={() => profile && findMatch(profile.username, seats)}
          >
            Find Match
          </button>
          {state.phase === 'error' && <p className="error">{state.message}</p>}
        </div>
      </div>
    );
  }

  const minutes = Math.floor(state.elapsedSeconds / 60);
  const seconds = state.elapsedSeconds % 60;

  return (
    <div className={styles.wrap}>
      <div className="card panel">
        <h2>Finding you an opponent…</h2>
        <p className={styles.elapsed}>{minutes}:{String(seconds).padStart(2, '0')}</p>
        <p className={styles.statusText}>
          {state.waitingCount > 1 ? `${state.waitingCount} players in queue` : 'Searching…'}
        </p>
        <div className={styles.actions}>
          <button type="button" className="secondary" onClick={cancel}>Cancel</button>
        </div>
        {state.timedOut && (
          <div className={styles.timeoutOptions}>
            <p className="muted">Still looking. You can keep waiting, or fill the remaining seats with bots.</p>
            <button type="button" className="primary" onClick={() => fillWithBots(seats)}>Fill with Bots</button>
          </div>
        )}
      </div>
    </div>
  );
}
