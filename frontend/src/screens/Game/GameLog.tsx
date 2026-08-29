// The persistent play-by-play narration log -- ported from the old
// frontend's ui/notifications.js logLine. Spectators always get this
// (they have no toasts/opponent-panel context of their own to fall back
// on); players only when the host left "Show game log" on (see
// gameState.showLogs, a fixed room setting).
import { useEffect, useRef } from 'react';
import type { LogEntry } from '../../types/game';
import styles from './Game.module.css';

export function GameLog({ entries }: { entries: LogEntry[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  return (
    <details className="card panel" open>
      <summary>Game log</summary>
      <div className={styles.log} ref={containerRef}>
        {entries.map((e) => <div key={e.id}>{e.text}</div>)}
      </div>
    </details>
  );
}
