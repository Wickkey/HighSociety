// Message shapes for the two WebSocket routes (/ws, /ws_spectate) and the
// small pure helpers around them. The IDENTIFY handshake and rematch
// messages are typed exhaustively since hooks/usePlayerConnection.ts and
// hooks/useSpectatorConnection.ts branch on them directly; every other
// in-game message type (auctions, moves, chat, ...) is Phase 3's concern --
// GenericGameMessage is the forward-compat catch-all those hooks forward
// upward unopened once that phase is ready to interpret them.

export interface IdentifyMessage {
  message_type: 'IDENTIFY';
  prompt: string;
}

export interface IdentifyErrorMessage {
  message_type: 'IDENTIFY_ERROR';
  prompt: string;
}

export interface IdentifySuccessMessage {
  message_type: 'IDENTIFY_SUCCESS';
  data?: { rejoin_token?: string; [key: string]: unknown };
}

export interface RematchMessage {
  message_type: 'REMATCH_UPDATE' | 'REMATCH_DECLINED' | 'REMATCH_STARTING';
  [key: string]: unknown;
}

export interface GenericGameMessage {
  message_type: string;
  /** Every _send() on the server side includes this (see web_server.py) --
   * plain narration text, sometimes empty, always present as a field. */
  prompt?: string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export type PlayerSocketMessage =
  | IdentifyMessage | IdentifyErrorMessage | IdentifySuccessMessage | RematchMessage | GenericGameMessage;

export type SpectatorSocketMessage =
  | IdentifyMessage | IdentifyErrorMessage | IdentifySuccessMessage | GenericGameMessage;

/** Who a connection is identifying as -- the IDENTIFY handshake asks for
 * either the account username or the display name depending on its prompt
 * text, distinguished by `resolveIdentifyAnswer` below. */
export interface JoinIdentity {
  username: string;
  name: string;
}

export function resolveIdentifyAnswer(prompt: string, identity: JoinIdentity): string {
  return /username/i.test(prompt) ? identity.username : identity.name;
}
