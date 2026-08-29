// Room code + shareable link, always visible while a room is in the lobby
// regardless of whether this tab has actually joined yet -- ported from the
// old frontend's playerList.js renderLobby (room-code-display/room-link-row)
// + lobby.js onCopyRoomLink.
import { useState } from 'react';
import type { RoomStatus } from '../../types/api';
import styles from './Room.module.css';

export function RoomCodeCard({ status }: { status: RoomStatus }) {
  const [copied, setCopied] = useState(false);
  const link = `${location.origin}/room/${encodeURIComponent(status.room_code)}`;
  const visibilityNote = status.visibility === 'private' ? ' (private, share this code with friends)' : ' (public)';

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      return; // clipboard permission denied/unavailable -- silently no-op, nothing else useful to do
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="card panel">
      <p>Room code: <strong>{status.room_code}</strong>{visibilityNote}</p>
      <div className={styles.linkRow}>
        <input type="text" readOnly value={link} onFocus={(e) => e.target.select()} />
        <button type="button" className="secondary" onClick={onCopy}>{copied ? 'Copied!' : 'Copy'}</button>
      </div>
    </div>
  );
}
