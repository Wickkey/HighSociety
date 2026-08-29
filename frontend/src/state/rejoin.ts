// Rejoin-token persistence -- ported from the old frontend's lobby.js
// (rejoinStorageKey/saveRejoinInfo/loadRejoinInfo/clearRejoinInfo). Kept as
// plain functions rather than folded into RoomContext: this is pure
// localStorage I/O with no React lifecycle of its own, read/written from
// hooks/usePlayerConnection.ts on specific socket events, not something a
// component subscribes to for re-renders.
export interface RejoinInfo {
  token: string;
  username: string;
  name: string;
}

function rejoinStorageKey(roomCode: string): string {
  return `hs_rejoin_${roomCode}`;
}

export function saveRejoinInfo(roomCode: string, token: string, username: string, name: string): void {
  try {
    localStorage.setItem(rejoinStorageKey(roomCode), JSON.stringify({ token, username, name }));
  } catch {
    // private mode / storage disabled -- reconnect-after-refresh just won't
    // work for this room, nothing else to fall back to.
  }
}

export function loadRejoinInfo(roomCode: string): RejoinInfo | null {
  let raw: string | null = null;
  try { raw = localStorage.getItem(rejoinStorageKey(roomCode)); } catch { /* private mode, etc. */ }
  if (!raw) return null;
  try {
    const info = JSON.parse(raw);
    return info?.token && info?.username ? { token: info.token, username: info.username, name: info.name ?? info.username } : null;
  } catch {
    return null;
  }
}

export function clearRejoinInfo(roomCode: string): void {
  try { localStorage.removeItem(rejoinStorageKey(roomCode)); } catch { /* private mode, etc. */ }
}
