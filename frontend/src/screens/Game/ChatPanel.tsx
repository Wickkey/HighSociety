// Player/spectator chat -- ported from the old frontend's ui/chat.js.
import { useEffect, useRef, useState } from 'react';
import type { LogEntry } from '../../types/game';
import styles from './Game.module.css';

export function ChatPanel({ entries, onSend }: { entries: LogEntry[]; onSend: (text: string) => void }) {
  const [text, setText] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  function send() {
    if (!text.trim()) return;
    onSend(text);
    setText('');
  }

  return (
    <div className="card panel">
      <h3>Chat</h3>
      <div className={styles.chatLog} ref={containerRef}>
        {entries.map((e) => <div key={e.id}>{e.text}</div>)}
      </div>
      <div className={styles.chatInputRow}>
        <input
          type="text" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
          placeholder="Say something…" autoComplete="off"
        />
        <button type="button" className="secondary" onClick={send}>Send</button>
      </div>
    </div>
  );
}
