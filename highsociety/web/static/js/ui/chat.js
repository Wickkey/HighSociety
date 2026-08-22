// Player/spectator chat log + send handlers.
import { $ } from '../utils/dom.js';
import { ws } from '../network/websocket.js';

// A CHAT message is never echoed back to its own sender over the wire (see
// PLAYING.md) — that's a relay-layer rule to avoid double-delivery, not a
// reason to leave the sender's own chat log blank. Appending it locally,
// formatted the same way an incoming one would be, keeps "did that actually
// send?" from ever being a question.
export function appendChatLine(elId, text) {
  const el = $(elId);
  const p = document.createElement('div');
  p.textContent = text;
  el.appendChild(p);
  el.scrollTop = el.scrollHeight;
}

export function onSpecChatSend() {
  const input = $('spec-chat-input');
  const text = input.value.trim();
  if (!text || !ws) return;
  // See web_server.py's _spectator_chat_listener: "spectators" reaches only
  // other spectators, anything else (including omitting the field) reaches
  // everyone — players included.
  const target = $('spec-chat-target-toggle').checked ? 'spectators' : 'all';
  ws.send(JSON.stringify({ message_type: 'CHAT', prompt: text, target }));
  appendChatLine('spec-chat-log', `💬 You${target === 'spectators' ? ' (spectators only)' : ''}: ${text}`);
  input.value = '';
}

export function onPlayerChatSend() {
  const input = $('player-chat-input');
  const text = input.value.trim();
  if (!text || !ws) return;
  // Reaches every other player + all spectators (see web_server.py's
  // _relay_player_chat) — no "target" selector for players, unlike
  // spectators, since chatting to a subset of the table doesn't make sense
  // from a player's own seat.
  ws.send(JSON.stringify({ message_type: 'CHAT', prompt: text }));
  appendChatLine('player-chat-log', `💬 You: ${text}`);
  input.value = '';
}
