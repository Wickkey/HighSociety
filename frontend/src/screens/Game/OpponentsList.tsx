// The opponents/players panel -- ported from the old frontend's
// gameRenderer.js renderOpponents. Ticks its own re-render every 500ms so
// each opponent's live per-turn countdown (anchored to turnStartedAt, the
// same math as this player's own move timer) actually looks live, without
// needing a real event to trigger it.
import { useEffect, useState } from 'react';
import type { GameState } from '../../types/game';
import { computePoints, orderedOpponentUsernames, urgentWindowSeconds } from '../../state/gameSelectors';
import { CardBack, CardFace } from './CardFace';
import styles from './Game.module.css';

export function OpponentsList({ gameState, isSpectator }: { gameState: GameState; isSpectator: boolean }) {
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 500);
    return () => clearInterval(id);
  }, []);

  const usernames = orderedOpponentUsernames(gameState, isSpectator);

  return (
    <div className={`card panel ${styles.opponentsList}`}>
      <h3>{isSpectator ? 'Players' : 'Opponents'}</h3>
      {usernames.map((username) => {
        const o = gameState.opponents[username];
        if (!o) return null;
        const isCurrentTurn = gameState.turnPlayer === username;
        const rowClasses = [styles.opponentRow];
        if (o.active === false) rowClasses.push(styles.inactive);
        if (o.outOfAuction) rowClasses.push(styles.outOfAuction);
        if (isCurrentTurn) rowClasses.push(styles.currentTurn);

        let statusSuffix = '';
        if (o.active === false) statusSuffix = ' (out)';
        else if (o.outOfAuction) statusSuffix = ' (passed)';

        const ptsLabel = gameState.revealCards
          ? `Points: ${computePoints(o.statusCards)}`
          : `${o.statusCards.length} card${o.statusCards.length === 1 ? '' : 's'}`;

        let timerText = '';
        let timerUrgent = false;
        if (isCurrentTurn && gameState.turnTimeLimit && gameState.turnStartedAt) {
          const remaining = Math.max(0, gameState.turnTimeLimit - (Date.now() - gameState.turnStartedAt) / 1000);
          timerText = `${Math.ceil(remaining)}s`;
          timerUrgent = remaining > 0 && remaining <= urgentWindowSeconds(gameState.turnTimeLimit);
        }

        return (
          <div key={username} className={rowClasses.join(' ')}>
            <div className={styles.opponentHeader}>
              <div className={styles.opponentHeaderLeft}>
                <span>{o.name}{statusSuffix}</span>
                {o.lastBid != null && <span className={styles.bidBadge}>Bid: {o.lastBid}</span>}
              </div>
              <div className={styles.opponentHeaderRight}>
                {timerText && <span className={`${styles.oppTimer} ${timerUrgent ? styles.urgent : ''}`}>{timerText}</span>}
                <span>{ptsLabel}</span>
              </div>
            </div>
            <div className={styles.chipRow}>
              {o.statusCards.map((c, i) => (gameState.revealCards ? <CardFace key={i} card={c} small /> : <CardBack key={i} />))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
