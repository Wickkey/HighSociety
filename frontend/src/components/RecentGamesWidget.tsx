// Home screen's "top 5 games" widget -- ported from gameHistory.js's
// loadHomeRecentGames, including the stale-while-revalidate cache shared
// with My Games (see api/gamesCache.ts) and the "only actually repaint if
// the content changed" guard that fixed the old frontend's own reported
// "annoying tiny refresh" bug.
import { useEffect, useRef, useState } from 'react';
import { peekGamesPage, fetchGamesPage } from '../api/gamesCache';
import type { RecentGame } from '../types/api';
import styles from '../screens/Home.module.css';

function opponentsLabel(game: RecentGame, myUsername: string): string {
  const others = game.opponents.filter((o) => o.name !== myUsername);
  if (others.length === 0) return 'Solo game';
  return others.map((o) => o.name).join(', ');
}

const DATE_FORMAT: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', year: 'numeric' };

export function RecentGamesWidget({ username, onOpenGame }: { username: string; onOpenGame: (gameId: number) => void }) {
  const [games, setGames] = useState<RecentGame[] | null>(() => peekGamesPage(username, 0)?.games.slice(0, 5) ?? null);
  const [entered, setEntered] = useState(false);
  const lastRenderedKey = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchGamesPage(username, 0).then((page) => {
      if (cancelled || !page) return;
      setGames(page.games.slice(0, 5));
    });
    return () => { cancelled = true; };
  }, [username]);

  if (!games || games.length === 0) return null;

  const key = JSON.stringify(games.map((g) => g.game_id));
  if (lastRenderedKey.current !== key) {
    lastRenderedKey.current = key;
    if (!entered) requestAnimationFrame(() => setEntered(true));
  }

  return (
    <div className={`card panel ${styles.recentGames} ${entered ? styles.enter : ''}`}>
      <h3>Recent Games</h3>
      <div>
        {games.map((g) => (
          <button
            key={g.game_id}
            type="button"
            className="recent-game-row"
            onClick={() => onOpenGame(g.game_id)}
          >
            <div>
              <span>{new Date(g.finished_at).toLocaleDateString('en-US', DATE_FORMAT)}</span>
              {' '}
              <span>#{g.placement}</span>
            </div>
            <div>{opponentsLabel(g, username)}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
