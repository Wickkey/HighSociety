// This player's own action panel -- money chips + bid controls, or the
// discard-a-painting controls, whichever the current prompt calls for, plus
// the move timer and Resign. Ported from the old frontend's
// gameRenderer.js (renderMovePanel/renderMoneyChips/updateBidStatus/
// renderPaintingChoices) + gameActions.js (onPlaceBid/onPass/
// onDiscardPainting/onResign).
import { useMoveTimer } from '../../hooks/useMoveTimer';
import type { UsePlayerGameSessionResult } from '../../hooks/usePlayerGameSession';
import { useConfirm } from '../../state/ConfirmDialogContext';
import styles from './Game.module.css';

export function MovePanel({ session }: { session: UsePlayerGameSessionResult }) {
  const { gameState, actions, connectionWarning } = session;
  const confirm = useConfirm();
  const { secondsLeft, isUrgent } = useMoveTimer(gameState.moveDeadline, gameState.turnTimeLimit, actions.expireMoveTimer);

  const pending = !gameState.myPrompt || gameState.myPrompt.answered;

  async function onResign() {
    const ok = await confirm('Are you sure you want to resign?', 'Resign');
    if (ok) actions.resign();
  }

  const addingTotal = gameState.selectedBid.reduce((a, b) => a + b, 0);

  return (
    <div className={`card panel ${styles.movePanel} ${pending ? styles.pending : ''}`}>
      <div className={styles.header}>
        <h3>Your move</h3>
        {secondsLeft !== null && (
          <span className={`${styles.moveTimer} ${isUrgent ? styles.urgent : ''}`}>{secondsLeft}s left</span>
        )}
      </div>

      {gameState.moveType === 'discard_painting' ? (
        <>
          <p className="muted">Taking a Faux Pas means discarding a Painting you own.</p>
          <div className={styles.chipRow}>
            {gameState.allowedPaintings.map((value) => (
              <button
                key={value}
                type="button"
                className={`${styles.moneyChip} ${gameState.selectedDiscardValue === value ? styles.selected : ''}`}
                onClick={() => actions.selectDiscardPainting(value)}
                disabled={pending}
              >
                {value}
              </button>
            ))}
          </div>
          <button
            type="button" className="primary"
            onClick={actions.discardPainting}
            disabled={pending || gameState.selectedDiscardValue === null}
          >
            Discard
          </button>
        </>
      ) : (
        <>
          <div className={styles.chipRow}>
            {[...gameState.myMoneyCards].sort((a, b) => a - b).map((value) => (
              <button
                key={value}
                type="button"
                className={`${styles.moneyChip} ${gameState.selectedBid.includes(value) ? styles.selected : ''}`}
                onClick={() => actions.selectMoneyCard(value)}
                disabled={pending}
              >
                {value}
              </button>
            ))}
          </div>
          <p className={styles.bidStatus}>
            Committed: {gameState.myAuctionBid} · Adding: {addingTotal} · New total: {gameState.myAuctionBid + addingTotal}
            {gameState.maxBid > 0 && ` (add more than ${gameState.maxBid - gameState.myAuctionBid} to raise)`}
          </p>
          <div className={styles.header}>
            <button type="button" className="primary" onClick={actions.placeBid} disabled={pending || gameState.selectedBid.length === 0}>
              Place Bid
            </button>
            <button type="button" className="secondary" onClick={actions.pass} disabled={pending}>Pass</button>
          </div>
        </>
      )}

      {gameState.moveError && <p className="error">{gameState.moveError}</p>}
      {connectionWarning && <p className={styles.warning}>{connectionWarning}</p>}

      <button type="button" className="danger" onClick={onResign} disabled={gameState.resigned}>
        {gameState.resigned ? 'Resigned' : 'Resign'}
      </button>
    </div>
  );
}
