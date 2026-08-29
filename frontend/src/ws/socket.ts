// Thin WebSocket wrapper shared by the player and spectator connection
// hooks (hooks/usePlayerConnection.ts, hooks/useSpectatorConnection.ts).
// Ported from the old frontend's network/websocket.js, with one
// improvement: the old code guarded a stale socket's asynchronously-late
// close event by comparing against a shared module-level `ws` binding
// (`if (ws === socket) ...`) since every caller reached into that one
// global. Here each connection closes over its own private `live` flag
// instead -- same guarantee (a superseded connection's own close event,
// which WebSocket always dispatches as a separate task rather than
// synchronously inside .close(), never fires handlers for a connection
// nobody's using anymore), no shared global required.
export interface SocketHandlers {
  onMessage: (data: unknown) => void;
  onClose: () => void;
}

export interface SocketConnection {
  send: (data: unknown) => void;
  /** Closes the socket AND suppresses its own close event from firing
   * `onClose` back -- use this whenever the caller is tearing the
   * connection down itself (unmount, explicit leave), so it doesn't get its
   * own teardown reported back as if the server had dropped the
   * connection. */
  dispose: () => void;
}

export function wsUrl(path: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${path}`;
}

export function connectSocket(path: string, handlers: SocketHandlers): SocketConnection {
  const socket = new WebSocket(wsUrl(path));
  let live = true;

  socket.onmessage = (evt) => {
    if (!live) return;
    handlers.onMessage(JSON.parse(evt.data));
  };
  socket.onclose = () => {
    if (!live) return;
    live = false;
    handlers.onClose();
  };

  return {
    send: (data: unknown) => socket.send(JSON.stringify(data)),
    dispose: () => {
      live = false;
      socket.close();
    },
  };
}
