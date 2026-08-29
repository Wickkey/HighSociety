// The live game screen for a spectator -- read-only: no MyPanel/MovePanel
// at all (a spectator has no seat of their own), the game log always shown
// (spectators have no toasts/opponent-panel context to fall back on
// otherwise). Ported from the old frontend's #screen-spectate.
import type { UseSpectatorGameSessionResult } from '../../hooks/useSpectatorGameSession';
import { AuctionPanel } from './AuctionPanel';
import { ChatPanel } from './ChatPanel';
import { GameLog } from './GameLog';
import { OpponentsList } from './OpponentsList';
import styles from './Game.module.css';

export function SpectateScreen({ session }: { session: UseSpectatorGameSessionResult }) {
  const { gameState } = session;

  return (
    <div className={styles.wrap}>
      <div className={styles.main}>
        <AuctionPanel gameState={gameState} isMyTurn={false} />
        <GameLog entries={gameState.log} />
        <ChatPanel entries={gameState.chat} onSend={(text) => session.sendChat(text)} />
      </div>
      <div className={styles.side}>
        <OpponentsList gameState={gameState} isSpectator />
      </div>
    </div>
  );
}
