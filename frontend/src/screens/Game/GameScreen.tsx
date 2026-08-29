// The live game screen for a seated player -- composes AuctionPanel/
// OpponentsList/MyPanel/MovePanel/GameLog/ChatPanel around one
// usePlayerGameSession. Ported from the old frontend's #screen-game.
import type { UsePlayerGameSessionResult } from '../../hooks/usePlayerGameSession';
import { AuctionPanel } from './AuctionPanel';
import { ChatPanel } from './ChatPanel';
import { GameLog } from './GameLog';
import { MovePanel } from './MovePanel';
import { MyPanel } from './MyPanel';
import { OpponentsList } from './OpponentsList';
import styles from './Game.module.css';

export function GameScreen({ session }: { session: UsePlayerGameSessionResult }) {
  const { gameState } = session;
  const isMyTurn = !!(gameState.myPrompt && !gameState.myPrompt.answered);

  return (
    <div className={styles.wrap}>
      <div className={styles.main}>
        <AuctionPanel gameState={gameState} isMyTurn={isMyTurn} />
        <MyPanel gameState={gameState} />
        <MovePanel session={session} />
        {gameState.showLogs && <GameLog entries={gameState.log} />}
        <ChatPanel entries={gameState.chat} onSend={session.actions.sendChat} />
      </div>
      <div className={styles.side}>
        <OpponentsList gameState={gameState} isSpectator={false} />
      </div>
    </div>
  );
}
