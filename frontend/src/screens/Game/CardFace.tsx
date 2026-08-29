// A single card face (or its hidden back) -- ported from the old frontend's
// gameRenderer.js cardEl/cardBackEl.
import type { GameCard } from '../../types/game';
import { cardLabel } from '../../state/gameSelectors';
import styles from './Game.module.css';

export function CardFace({ card, big, small }: { card: GameCard; big?: boolean; small?: boolean }) {
  const { tone, text } = cardLabel(card);
  const size = big ? styles.big : small ? styles.small : '';
  return (
    <div className={`${styles.card} ${tone === 'green' ? styles.green : ''} ${size}`} title={card.description}>
      {text}
      {card.is_green && <span className={styles.greenDot} />}
    </div>
  );
}

export function CardBack() {
  return <div className={styles.cardBack} title="Hidden. Cards are hidden for this room." />;
}
