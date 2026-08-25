// The finished screen (standings + trophy) and the rematch request/vote flow.
//
// Circular import note (see network/messages.js's own comment for the full
// reasoning): network/messages.js imports handleRematchMessage from this
// file, and network/websocket.js imports from messages.js -- so this file
// importing `ws` back from websocket.js is a cycle, but the same safe shape
// used throughout this module graph, since `ws` is only ever read inside a
// function body here, never at this module's own top-level evaluation.
// game/gameState.js, game/gameEvents.js, and game/gameRenderer.js import
// nothing from this file, so those three imports below are plain, ordinary,
// non-circular static imports.
import { $, hide, show, showError, showScreen } from '../utils/dom.js';
import { escapeHtml } from '../utils/formatting.js';
import { clearRejoinInfo, currentRoomCode, fetchJSON, lastStatus } from './lobby.js';
import { openProfileModal } from '../ui/modals.js';
import { ws } from '../network/websocket.js';
import { game, resetGameState, seedOpponents, applyRoomDisplaySettings } from '../game/gameState.js';
import { ensureGameScreenVisible } from '../game/gameEvents.js';
import { renderOpponents } from '../game/gameRenderer.js';

// Rematch panel state on the finished screen: null while no vote is
// underway, else {requestedBy, botMix, votes} mirroring web_server.py's
// GameRoom.rematch shape (see REMATCH_UPDATE). rematchBotSeats/
// rematchDefaultBotMix come from the /api/status payload (or a live status
// refresh) each time the finished screen renders — the server, not this
// file, owns how many bot seats are actually available and what they
// default to (see _status_payload's "rematch_bot_seats"/
// "rematch_default_bot_mix").
let currentRematch = null;
let rematchBotSeats = 0;
let rematchDefaultBotMix = [];

export function renderFinished(status) {
  clearRejoinInfo(currentRoomCode()); // game's over — nothing left to reconnect to
  showScreen('screen-finished');
  // The money-eliminated player (see gameplay.py's determine_winner —
  // distinct from simply "didn't have the highest score") was never in
  // contention at all, so they're sorted to the very bottom regardless of
  // points; everyone else sorts by points as before.
  const standings = (status.final_standings || []).slice().sort((a, b) => {
    if (!!a.eliminated !== !!b.eliminated) return a.eliminated ? 1 : -1;
    return b.points - a.points;
  });
  const winners = new Set(status.winners || []);

  const trophy = $('finished-trophy');
  if (winners.size === 1) {
    trophy.textContent = '🏆';
    $('finished-headline').textContent = `${[...winners][0]} wins!`;
  } else if (winners.size > 1) {
    trophy.textContent = '🤝';
    $('finished-headline').textContent = `Tie: ${[...winners].join(', ')}`;
  } else {
    trophy.textContent = '🎲';
    $('finished-headline').textContent = 'Game over';
  }
  // Restart the entrance animation even if this exact status was already
  // rendered once (e.g. a stray poll) — removing and re-adding the class
  // forces the browser to replay the @keyframes rather than no-op.
  trophy.classList.remove('enter');
  void trophy.offsetWidth; // eslint-disable-line no-unused-expressions -- force reflow so the class removal actually takes effect first
  trophy.classList.add('enter');

  const rows = standings.map((s, i) => {
    const isWinner = winners.has(s.username);
    const state = isWinner ? 'winner' : s.eliminated ? 'eliminated' : (s.active === false ? 'inactive' : 'lost');
    const tag = isWinner ? ' 🏆'
      : s.eliminated ? ' <span class="standing-tag" title="Eliminated from winning for having the least money left">(least money)</span>'
      : (s.active === false ? ' (left the game)' : '');
    return `
    <div class="standing-row ${state}" style="animation-delay: ${i * 90}ms">
      <span class="name"><button type="button" class="name-link" data-open-profile>${escapeHtml(s.username)}</button>${tag}</span>
      <span>Points: ${s.points}</span>
      <span>Money left: ${s.money_left}</span>
    </div>`;
  }).join('');
  $('standings-table').innerHTML = rows || '<p class="muted">No standings available.</p>';

  currentRematch = status.rematch || null;
  rematchBotSeats = status.rematch_bot_seats || 0;
  rematchDefaultBotMix = status.rematch_default_bot_mix || [];
  renderRematchPanel();
}

// Only a still-connected player (as opposed to a spectator, or a player
// viewing the results via a status poll with no live socket) has a channel
// to actually request/vote on a rematch over, so this hides the whole
// panel for anyone else rather than showing controls that couldn't do
// anything.
export function renderRematchPanel() {
  const panel = $('rematch-panel');
  if (!ws || ws.readyState !== WebSocket.OPEN || !game.myUsername) {
    hide(panel);
    return;
  }
  show(panel);
  hide($('rematch-error'));

  const requestBtn = $('btn-request-rematch');
  const form = $('rematch-bot-form');
  const statusBox = $('rematch-status');
  const voteActions = $('rematch-vote-actions');

  if (!currentRematch) {
    show(requestBtn);
    hide(form);
    hide(statusBox);
    return;
  }

  hide(requestBtn);
  hide(form);
  show(statusBox);

  const { requestedBy, votes } = currentRematch;
  const iHaveAccepted = votes[game.myUsername] === true;
  const waitingOn = Object.keys(votes).filter((n) => votes[n] !== true);

  if (iHaveAccepted) {
    hide(voteActions);
    $('rematch-status-text').textContent = waitingOn.length
      ? `Waiting on ${waitingOn.join(', ')} to accept the rematch…`
      : 'Everyone accepted. Starting the rematch…';
  } else {
    show(voteActions);
    $('rematch-status-text').textContent = `${requestedBy} wants a rematch. Accept?`;
  }
}

function fillRematchBotForm(mix) {
  const counts = { easy: 0, medium: 0, hard: 0 };
  mix.forEach((b) => { if (counts[b] !== undefined) counts[b] += 1; });
  $('rematch-bot-easy').value = counts.easy;
  $('rematch-bot-medium').value = counts.medium;
  $('rematch-bot-hard').value = counts.hard;
  $('rematch-bot-hint').textContent = rematchBotSeats
    ? `${rematchBotSeats} bot seat${rematchBotSeats === 1 ? '' : 's'} to fill (same as last time by default, but changeable).`
    : 'No bot seats this time. Every seat is a returning player.';
}

export function onRequestRematchClick() {
  if (rematchBotSeats > 0) {
    fillRematchBotForm(rematchDefaultBotMix);
    hide($('btn-request-rematch'));
    show($('rematch-bot-form'));
  } else {
    sendRematchRequest([]);
  }
}

export function onCancelRematchForm() {
  hide($('rematch-bot-form'));
  show($('btn-request-rematch'));
}

export function onSendRematchRequest() {
  const counts = {
    easy: parseInt($('rematch-bot-easy').value || '0', 10),
    medium: parseInt($('rematch-bot-medium').value || '0', 10),
    hard: parseInt($('rematch-bot-hard').value || '0', 10),
  };
  const botMix = [];
  for (const [type, n] of Object.entries(counts)) for (let i = 0; i < n; i += 1) botMix.push(type);
  if (botMix.length !== rematchBotSeats) {
    showError($('rematch-error'), `Choose exactly ${rematchBotSeats} bot${rematchBotSeats === 1 ? '' : 's'} total.`);
    return;
  }
  sendRematchRequest(botMix);
}

function sendRematchRequest(botMix) {
  hide($('rematch-bot-form'));
  ws.send(JSON.stringify({ message_type: 'REMATCH_REQUEST', data: { bot_mix: botMix } }));
}

export function onAcceptRematch() {
  ws.send(JSON.stringify({ message_type: 'REMATCH_VOTE', data: { accept: true } }));
}

export function onDeclineRematch() {
  ws.send(JSON.stringify({ message_type: 'REMATCH_VOTE', data: { accept: false } }));
}

function showRematchDeclinedNotice(declinedBy) {
  show($('rematch-status'));
  hide($('rematch-vote-actions'));
  $('rematch-status-text').textContent = `${declinedBy} declined the rematch.`;
  // The next real update (a fresh request, or none at all) re-renders this
  // properly; this is just a few seconds of "here's what just happened"
  // before falling back to the normal "Request Rematch" state.
  setTimeout(() => { if (!currentRematch) renderRematchPanel(); }, 4000);
}

// Called by network/messages.js for REMATCH_UPDATE/REMATCH_DECLINED/
// REMATCH_STARTING -- kept as one entry point so messages.js doesn't need
// to know these three cases' individual handling, just that they're
// "rematch messages."
export function handleRematchMessage(msg) {
  if (msg.message_type === 'REMATCH_UPDATE') {
    currentRematch = { requestedBy: msg.data.requested_by, botMix: msg.data.bot_mix, votes: msg.data.votes };
    renderRematchPanel();
    return;
  }
  if (msg.message_type === 'REMATCH_DECLINED') {
    currentRematch = null;
    showRematchDeclinedNotice(msg.data.declined_by);
    return;
  }
  // REMATCH_STARTING
  currentRematch = null;
  const myUsername = game.myUsername;
  // Passing lastStatus() here (not null) matters: reveal_cards/show_logs/
  // turn_time_limit are GameRoom-level settings _maybe_start_rematch never
  // touches (only bot_mix/players/state/seed change for a rematch), so the
  // finished-game status this already holds is still exactly right for
  // those three -- no need to wait on a fresh fetch. Passing null made
  // resetGameState fall back to its own hardcoded defaults (reveal_cards/
  // show_logs both true, no timer) regardless of what the room was
  // actually configured with -- a real, live-reported bug ("logs were
  // off, then a rematch turned them back on").
  resetGameState(myUsername, lastStatus());
  // Seed is the one exception: _maybe_start_rematch deliberately re-rolls
  // it for every rematch (a fresh shuffle for a fresh game -- see its own
  // comment), so lastStatus()'s seed is already stale by the time this
  // arrives. The server hands over the actual new one right here instead
  // of requiring a second round trip just to display it correctly.
  if (msg.data && msg.data.seed != null) {
    game.seed = msg.data.seed;
    applyRoomDisplaySettings(); // resetGameState already called this once with the stale seed baked in
  }
  // Resign works anytime once in a game (see gameActions.js's onResign) --
  // re-enable it for the fresh game immediately rather than waiting for
  // its first PLAYER_STATE/PLAYER_MOVE.
  $('btn-resign').disabled = false;
  ensureGameScreenVisible(false);
  fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode())}`).then((status) => {
    seedOpponents(status, myUsername);
    renderOpponents(false);
  }).catch(() => {});
}

// Wired from index.html's standings-table click delegation (see app.js).
export function onStandingsTableClick(e) {
  const btn = e.target.closest('[data-open-profile]');
  if (btn) openProfileModal(btn.textContent.trim());
}
