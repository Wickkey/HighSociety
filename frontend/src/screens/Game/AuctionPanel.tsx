// The shared auction display (round, whose turn, the card up for auction,
// the current max bid) -- ported from the old frontend's gameRenderer.js
// renderAuctionPanel. Used by both GameScreen and SpectateScreen.
import type { GameState } from '../../types/game';
import { cardTypeName } from '../../state/gameSelectors';
import { CardFace } from './CardFace';
import styles from './Game.module.css';

export function AuctionPanel({ gameState, isMyTurn }: { gameState: GameState; isMyTurn: boolean }) {
  const { round, card, maxBid, turnPlayer, myUsername } = gameState;

  return (
    <div className="card panel">
      <div className={styles.header}>
        <h2>{round ? `Auction #${round}` : 'Waiting for the first auction…'}</h2>
        {(isMyTurn || turnPlayer) && (
          <span className={styles.turnLabel}>
            <span className={styles.turnDot} />
            {isMyTurn ? 'Your turn' : `${turnPlayer}'s turn`}
          </span>
        )}
      </div>
      {card && (
        <div className={styles.auctionCard}>
          <span className={styles.cardTypeLabel}>{cardTypeName(card)}</span>
          <CardFace card={card} big />
          <div className={styles.bidRow}>
            <span>Current bid:</span>
            <span className={styles.bidValue}>{maxBid || 0}</span>
          </div>
        </div>
      )}
      {!card && myUsername === null && <p className="muted">The game is starting…</p>}
    </div>
  );
}
