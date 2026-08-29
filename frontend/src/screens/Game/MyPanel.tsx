// This player's own points + won status cards -- ported from the old
// frontend's gameRenderer.js renderMyPanel.
import type { GameState } from '../../types/game';
import { CardFace } from './CardFace';
import styles from './Game.module.css';

export function MyPanel({ gameState }: { gameState: GameState }) {
  return (
    <div className={`card panel ${styles.myPanel}`}>
      <div className={styles.myPanelHeader}>
        <strong>{gameState.myUsername}</strong>
        <span>Points: {gameState.myPoints}</span>
      </div>
      <div className={styles.chipRow}>
        {gameState.myStatusCards.map((c, i) => <CardFace key={i} card={c} small />)}
        {gameState.myStatusCards.length === 0 && <span className="muted">No cards yet</span>}
      </div>
    </div>
  );
}
