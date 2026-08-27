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
import { clearRejoinInfo, currentRoomCode, fetchJSON, lastStatus, loadRejoinInfo, setGameFinished } from './lobby.js';
import { openProfileModal } from '../ui/modals.js';
import { ws } from '../network/websocket.js';
import { game, resetGameState, seedOpponents, applyRoomDisplaySettings } from '../game/gameState.js';
import { ensureGameScreenVisible } from '../game/gameEvents.js';
import { renderOpponents } from '../game/gameRenderer.js';
import { loadProfile } from '../auth/profile.js';

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
  if (!game) {
    // `game` is still its module-level `null` default -- this is a cold
    // arrival at an already-finished game (a page reload, a mobile browser
    // reclaiming a backgrounded tab, reopening a saved room link, ...)
    // where app.js's DOMContentLoaded handler called refreshStatus() from
    // the URL's ?room= before any onJoin/attemptReconnect ever ran
    // resetGameState. Every line below (and revealEloChange further down)
    // assumes `game` exists -- most visibly game.myUsername, read
    // unconditionally by renderRematchPanel() a few lines down -- so
    // without this, that read throws on a bare `null` and the whole
    // reveal never even starts. Recover "my" username the same way
    // attemptReconnect() does, from this room's stored rejoin info, before
    // clearRejoinInfo below wipes it; null (no stored info, e.g. arriving
    // via a bare spectate link) is exactly the existing spectator case
    // every myUsername check elsewhere already handles.
    const info = loadRejoinInfo(currentRoomCode());
    resetGameState(info ? info.username : null, status);
  }
  clearRejoinInfo(currentRoomCode()); // game's over — nothing left to reconnect to
  setGameFinished(true); // see lobby.js's own comment on why this can't just be inferred from the current screen
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

  // Guarded by game_id, not just game.myUsername -- renderFinished itself
  // can legitimately run more than once for the exact same finished game
  // (a stray status poll, or a reconnect blip around game-end independently
  // triggering its own refreshStatus() -- see websocket.js's onclose and
  // gameEvents.js's game_over handler, both of which can call it). Every
  // repeat call used to restart revealEloChange from scratch: hiding
  // whatever it had just shown, bumping eloRevealToken, and racing a brand
  // new poll against whichever call happens to finish last -- if that last
  // one loses the race (e.g. its own poll hasn't reached the DB write yet
  // even though an earlier call already had the answer), the token guard
  // makes it silently discard the earlier call's good result and the card
  // never gets a chance to show anything. Only the first call for a given
  // game_id may ever start the cycle; later ones for that same game are a
  // no-op here (whatever the first call already resolved to stays showing).
  if (game.myUsername && status.game_id !== revealedGameId) {
    revealedGameId = status.game_id;
    revealEloChange(status);
  }
}

// The game_id revealEloChange has already been started for -- see
// renderFinished's own comment on why a repeat call for the same game must
// never restart it. null until the first finished render of any game.
let revealedGameId = null;

// A per-game token, bumped every time revealEloChange starts -- so a
// rematch (which calls renderFinished again for a brand new game) can't
// have its OLD poll loop's late response land on top of the NEW game's
// card once it's already showing something else. Simpler than threading
// an AbortController through fetchJSON for what's already a short,
// bounded retry loop.
let eloRevealToken = 0;

// See index.html's three elo-reveal-* elements for the state machine
// this drives: a brief "calculating" placeholder (shown immediately --
// the game-history write is fire-and-forget, see game_history.py's own
// docstring on why), replaced once /api/status's elo_changes stops being
// null by either the real numbers or a guest sign-in nudge in the same
// slot. Only ever shows *this* player's own change (chess.com/colonist.io
// both treat a post-game rating reveal as personal, not table-wide).
async function revealEloChange(initialStatus) {
  const token = ++eloRevealToken;
  const calculating = $('elo-reveal-calculating');
  const reveal = $('elo-reveal');
  const guestNote = $('elo-reveal-guest-note');
  hide(reveal);
  hide(guestNote);
  showEnter(calculating);

  let changes = initialStatus.elo_changes;
  for (let attempt = 0; attempt < 8 && (changes === undefined || changes === null); attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    if (token !== eloRevealToken) return; // superseded by a newer game (e.g. a rematch)
    try {
      const fresh = await fetchJSON(`/api/status?room=${encodeURIComponent(currentRoomCode())}`);
      changes = fresh.elo_changes;
    } catch (e) { /* transient network hiccup -- next attempt (or the timeout below) handles it */ }
  }
  if (token !== eloRevealToken) return;
  hide(calculating);

  const mine = changes && changes[game.myUsername];
  const profile = loadProfile();
  if (mine) {
    showEloNumbers(mine.old_rating, mine.new_rating, mine.rating_change);
  } else if (!profile || !profile.google_id) {
    // A guest never actually gets a rating change -- the same slot that
    // would show one instead nudges them to sign in, turning this into a
    // conversion moment rather than just showing nothing.
    showEnter(guestNote);
  }
  // Neither: e.g. a Google-linked human whose table had no other rated
  // participant that game (compute_elo_deltas needs 2+) -- nothing
  // meaningful to show, so the card just stays hidden.
}

function showEnter(el) {
  el.classList.remove('hidden', 'enter');
  void el.offsetWidth; // force reflow so the class removal above actually takes effect first
  el.classList.add('enter');
}

function showEloNumbers(oldRating, newRating, delta) {
  const card = $('elo-reveal');
  card.classList.remove('gain', 'loss');
  if (delta > 0) card.classList.add('gain');
  else if (delta < 0) card.classList.add('loss');
  $('elo-reveal-old').textContent = oldRating;
  $('elo-reveal-new').textContent = oldRating; // count-up starts here, see animateEloNumber
  $('elo-reveal-delta').textContent = delta > 0 ? `+${delta}` : `${delta}`;
  showEnter(card);
  animateEloNumber($('elo-reveal-new'), oldRating, newRating);
}

// No charting-library-style dependency for this (matching this session's
// own hand-rolled-sparkline precedent) -- just a plain
// requestAnimationFrame loop easing from old_rating to new_rating,
// landing exactly on the real integer regardless of frame timing.
function animateEloNumber(el, from, to, durationMs = 1100) {
  const start = performance.now();
  function tick(now) {
    const t = Math.min(1, (now - start) / durationMs);
    const eased = 1 - (1 - t) ** 3; // ease-out cubic
    el.textContent = Math.round(from + (to - from) * eased);
    if (t < 1) requestAnimationFrame(tick);
    else el.textContent = to;
  }
  requestAnimationFrame(tick);
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
    // Always false in practice (rematches always auto-roll, see
    // _maybe_start_rematch's own comment) but read from the server
    // rather than hardcoded, so this stays correct if that ever changes.
    game.manualSeed = !!msg.data.manual_seed;
    applyRoomDisplaySettings(); // resetGameState already called this once with the stale seed baked in
  }
  // btn-resign's re-enable now lives inside resetGameState itself (see its
  // own comment) so every fresh game gets it, not just a rematch.
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
