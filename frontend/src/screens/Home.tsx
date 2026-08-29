// Tile picker + Join/Host/Rules sub-panels -- ported from index.html's
// #screen-host-setup + lobby.js's onCreateGame/onJoinByCode/loadHomeGlobalStats.
// Room lobby/waiting (what happens right after Join or Host actually
// succeeds) is Phase 2 -- for now, a successful create/join just shows the
// room code it got back, proving the API round-trip works end to end.
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { RecentGamesWidget } from '../components/RecentGamesWidget';
import { usePolling } from '../hooks/usePolling';
import { useProfile } from '../state/ProfileContext';
import type { GlobalStats, RoomSummary } from '../types/api';
import styles from './Home.module.css';

type SubPanel = 'join' | 'host' | 'rules' | null;

const TIME_LIMITS = [
  [{ label: 'No limit', value: '' }],
  [{ label: '15s', value: '15' }, { label: '30s', value: '30' }, { label: '60s', value: '60' }],
  [{ label: '90s', value: '90' }, { label: '2 min', value: '120' }],
];

export function Home() {
  const { panel } = useParams<{ panel?: string }>();
  const activePanel: SubPanel = panel === 'join' || panel === 'host' || panel === 'rules' ? panel : null;
  const navigate = useNavigate();
  const { profile } = useProfile();
  const [globalStats, setGlobalStats] = useState<GlobalStats | null>(null);

  useEffect(() => {
    if (activePanel) return; // only the tile picker itself shows the sticky footer -- a sub-panel has no room/reason for it
    api.globalStats().then(setGlobalStats).catch(() => setGlobalStats(null));
  }, [activePanel]);

  if (activePanel === 'join') return <JoinPanel onBack={() => navigate('/')} />;
  if (activePanel === 'host') return <HostPanel onBack={() => navigate('/')} />;
  if (activePanel === 'rules') return <RulesPanel onBack={() => navigate('/')} />;

  return (
    <>
      <div className={styles.tiles}>
        <button type="button" className={`card ${styles.tile}`} onClick={() => navigate('/host-setup/join')}>
          <span className={styles.tileIcon}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M10.5 20H6.5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4" />
              <path d="M15.2 8.3 19 12l-3.8 3.7" />
              <path d="M19 12H9.5" />
            </svg>
          </span>
          <span className={styles.tileTitle}>Join a Game</span>
          <span className={styles.tileSub}>Public games or a room code</span>
        </button>
        <button type="button" className={`card ${styles.tile}`} onClick={() => navigate('/host-setup/host')}>
          <span className={`${styles.tileIcon} ${styles.tileIconLarge}`}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M4 18 L5 10 L8.5 13.5 L12 6.5 L15.5 13.5 L19 10 L20 18 Z" />
              <path d="M6 21h12" />
            </svg>
          </span>
          <span className={styles.tileTitle}>Host a New Game</span>
          <span className={styles.tileSub}>Set up seats, bots, and rules</span>
        </button>
        <button type="button" className={`card ${styles.tile}`} onClick={() => navigate('/host-setup/rules')}>
          <span className={styles.tileIcon}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 7.2c-2.1-1.4-4.9-1.9-8.2-1.6v13c3.3-.3 6.1.2 8.2 1.6 2.1-1.4 4.9-1.9 8.2-1.6v-13c-3.3-.3-6.1.2-8.2 1.6z" />
              <path d="M12 7.2v13" />
            </svg>
          </span>
          <span className={styles.tileTitle}>Game Rules</span>
          <span className={styles.tileSub}>New to High Society? Start here</span>
        </button>
      </div>

      {profile && (
        <RecentGamesWidget
          username={profile.username}
          onOpenGame={(id) => console.info('game detail modal -- Phase 4', id)}
        />
      )}

      {globalStats && (
        <div className={styles.globalStats}>
          <div className={styles.globalStat}>
            <span className={styles.globalStatValue}>{globalStats.total_games}</span>
            <span className={styles.globalStatLabel}>Games Played</span>
          </div>
          <div className={styles.globalStat}>
            <span className={styles.globalStatValue}>{globalStats.total_players}</span>
            <span className={styles.globalStatLabel}>Players</span>
          </div>
        </div>
      )}
    </>
  );
}

function BackButton({ onBack }: { onBack: () => void }) {
  return <button type="button" className={styles.backButton} onClick={onBack}>← Back</button>;
}

function JoinPanel({ onBack }: { onBack: () => void }) {
  const [rooms, setRooms] = useState<RoomSummary[] | null>(null);
  const [roomCode, setRoomCode] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();

  usePolling(() => { api.rooms().then((r) => setRooms(r.rooms)).catch(() => {}); }, 2000);

  function joinRoom(code: string) {
    // The real "enter your name and connect" join screen is Phase 2 (it
    // needs the WebSocket connection this phase deliberately doesn't
    // build yet) -- for now this just proves the room code round-trips.
    navigate(`/room/${encodeURIComponent(code)}`);
  }

  return (
    <div className="card panel">
      <BackButton onBack={onBack} />
      <h2>Join a game</h2>
      <p className="muted">Pick a public game, or enter a room code.</p>
      <div className={styles.roomsList}>
        {rooms === null && <p className="muted">Loading public games…</p>}
        {rooms?.length === 0 && <p className="muted">No public games right now.</p>}
        {rooms?.map((r) => (
          <div key={r.room_code} className={styles.roomRow}>
            <span>{r.room_code} — {r.joined}/{r.seats} seats</span>
            <button type="button" className="secondary" onClick={() => joinRoom(r.room_code)}>Join</button>
          </div>
        ))}
      </div>
      <label>
        Room code
        <input
          type="text" value={roomCode} onChange={(e) => setRoomCode(e.target.value)}
          placeholder="e.g. AB3D9" autoComplete="off" maxLength={10}
        />
      </label>
      <button
        type="button" className="secondary"
        onClick={() => { const code = roomCode.trim().toUpperCase(); if (!code) { setError('Enter a room code.'); return; } joinRoom(code); }}
      >
        Join
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function HostPanel({ onBack }: { onBack: () => void }) {
  const { profile } = useProfile();
  const navigate = useNavigate();
  const [seats, setSeats] = useState(3);
  const [turnTimeLimit, setTurnTimeLimit] = useState('');
  const [visibility, setVisibility] = useState<'public' | 'private'>('public');
  const [botCounts, setBotCounts] = useState({ easy: 0, medium: 1, hard: 0 });
  const [revealCards, setRevealCards] = useState(true);
  const [showLogs, setShowLogs] = useState(true);
  const [botThinkTime, setBotThinkTime] = useState(1.5);
  const [seed, setSeed] = useState('');
  const [error, setError] = useState('');

  async function onCreateGame() {
    setError('');
    const botMix = ([...Array(botCounts.easy).fill('easy'), ...Array(botCounts.medium).fill('medium'), ...Array(botCounts.hard).fill('hard')]);
    try {
      const status = await api.createGame({
        seats,
        bot_mix: botMix,
        bot_think_time: botThinkTime,
        visibility,
        turn_time_limit: turnTimeLimit ? parseFloat(turnTimeLimit) : null,
        reveal_cards: revealCards,
        show_logs: showLogs,
        host_username: profile?.username ?? null,
        ...(seed ? { seed: parseInt(seed, 10) } : {}),
      });
      navigate(`/room/${encodeURIComponent(status.room_code)}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="card panel">
      <BackButton onBack={onBack} />
      <h2>Host a new game</h2>

      <label>
        Total seats
        <input type="number" min={2} max={5} value={seats} onChange={(e) => setSeats(Number(e.target.value))} />
      </label>

      <div className={styles.fieldBlock}>
        <span className={styles.fieldBlockLabel}>Time per move</span>
        <div className={styles.timeLimitButtons}>
          {TIME_LIMITS.map((row, i) => (
            <div className={styles.timeLimitRow} key={i}>
              {row.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={`${styles.timeLimitButton} ${turnTimeLimit === opt.value ? styles.timeLimitButtonSelected : ''}`}
                  onClick={() => setTurnTimeLimit(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
      <p className={styles.hint}>If set, each player gets this many seconds per bid/pass before they're auto-passed.</p>

      <fieldset className={styles.fieldset}>
        <legend>Who can join?</legend>
        <div className={styles.row}>
          <label className={`${styles.radioLabel} ${visibility === 'public' ? styles.radioLabelChecked : ''}`}>
            <input type="radio" name="visibility" checked={visibility === 'public'} onChange={() => setVisibility('public')} />
            <span className={styles.radioLabelText}><strong>Public</strong><span className={styles.hint}>Anyone can join</span></span>
          </label>
          <label className={`${styles.radioLabel} ${visibility === 'private' ? styles.radioLabelChecked : ''}`}>
            <input type="radio" name="visibility" checked={visibility === 'private'} onChange={() => setVisibility('private')} />
            <span className={styles.radioLabelText}><strong>Private</strong><span className={styles.hint}>Requires room code</span></span>
          </label>
        </div>
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend>Fill seats with bots (optional)</legend>
        <div className={styles.row}>
          {(['easy', 'medium', 'hard'] as const).map((tier) => (
            <label key={tier}>
              {tier[0].toUpperCase() + tier.slice(1)}
              <input
                type="number" min={0} max={4} value={botCounts[tier]}
                onChange={(e) => setBotCounts((c) => ({ ...c, [tier]: Number(e.target.value) }))}
              />
            </label>
          ))}
        </div>
        <p className={styles.hint}>At least one seat must be left for a human (you).</p>
      </fieldset>

      <details className={styles.advanced}>
        <summary>Advanced</summary>
        <fieldset className={styles.fieldset}>
          <legend>Table settings</legend>
          <div className={styles.row}>
            <label className={styles.radioLabel}>
              <input type="checkbox" checked={revealCards} onChange={(e) => setRevealCards(e.target.checked)} /> Show opponents&apos; paintings
            </label>
            <label className={styles.radioLabel}>
              <input type="checkbox" checked={showLogs} onChange={(e) => setShowLogs(e.target.checked)} /> Show game log
            </label>
          </div>
          <p className={styles.hint}>Fixed for the whole table once the game starts. Not adjustable mid-game.</p>
        </fieldset>
        <label>
          Bot think time (seconds)
          <input type="number" min={0} step={0.1} value={botThinkTime} onChange={(e) => setBotThinkTime(Number(e.target.value))} />
        </label>
        <label>
          Random seed (optional)
          <input type="number" step={1} placeholder="random" value={seed} onChange={(e) => setSeed(e.target.value)} />
        </label>
        <p className={styles.hint}>Leave blank for a random game. Setting a seed makes the deck reproducible.</p>
      </details>

      <button type="button" className={`primary ${styles.hostGameButton}`} onClick={onCreateGame}>Host Game</button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function RulesPanel({ onBack }: { onBack: () => void }) {
  return (
    <div className="card panel">
      <BackButton onBack={onBack} />
      <h2>How to play</h2>
      <p>
        <strong>Goal:</strong> End the game with the most points — but there&apos;s a catch.
        When the game ends, whoever has the <strong>least money left</strong> is eliminated
        from winning entirely. If two or more players are tied for the least money, none of
        them are eliminated — everyone stays in contention. Only then, among whoever remains,
        does the highest score win.
      </p>
      <p>Each round, one card is auctioned. There are two opposite auction types:</p>
      <p><strong>Normal auction</strong> (Paintings, the Prestige Card) — Players take turns raising the bid. Passing means you&apos;re out. The last player standing wins the card and pays their bid.</p>
      <p><strong>Disgrace auction</strong> (Faux Pas, Passe, Scandale) — Same bidding, but the first player to pass gets stuck with the card and gets their money back. Everyone else forfeits their raised money.</p>
      <p className={styles.hint}>Money cards are locked in once committed to a bid — you can add more on a later turn to raise, but can&apos;t take any back until the auction resolves.</p>
      <p className={styles.hint}>Green cards (Prestige, Scandale) can end the game early: once the 4th green card is revealed, the game stops right there, even if cards remain.</p>
    </div>
  );
}
