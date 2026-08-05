// HighSociety web frontend — vanilla JS, no build step and no external
// dependencies. Talks the exact JSON message protocol described in
// BOT_API.md/network/protocol.py: this is "just another remote player",
// same as network_client.py, over a WebSocket instead of a raw socket.

const $ = (id) => document.getElementById(id);

function showScreen(id) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.add('hidden'));
  $(id).classList.remove('hidden');
}

function hide(el) { el.classList.add('hidden'); }
function showError(el, text) { el.textContent = text; el.classList.remove('hidden'); }

function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${path}`;
}

function setBadge(text) {
  const badge = $('connection-badge');
  badge.textContent = text;
  badge.classList.remove('hidden');
}

// ---------------------------------------------------------------- cards --

function describeCard(card) {
  const names = { Painting: `Painting (${card.value})`, PrestigeCard: 'Prestige Card (×2)',
    FauxPas: 'Faux Pas', Passe: 'Passe (−5)', Scandale: 'Scandale (½×, green)' };
  return names[card.type] || card.type;
}

function cardLabel(card) {
  switch (card.type) {
    case 'Painting': return { cls: 'painting', text: String(card.value) };
    case 'PrestigeCard': return { cls: 'prestige', text: '×2' };
    case 'FauxPas': return { cls: 'disgrace', text: 'Faux Pas' };
    case 'Passe': return { cls: 'disgrace', text: '−5' };
    case 'Scandale': return { cls: 'disgrace green', text: '½×' };
    default: return { cls: '', text: card.type };
  }
}

function cardEl(card, big) {
  const { cls, text } = cardLabel(card);
  const div = document.createElement('div');
  div.className = `status-card ${cls}${big ? ' big' : ''}`;
  div.innerHTML = `<span class="value">${text}</span>${card.is_green ? '<span class="green-dot"></span>' : ''}`;
  if (card.description) div.title = card.description;
  return div;
}

function cardBackEl() {
  const div = document.createElement('div');
  div.className = 'card-back';
  div.title = 'Hidden — enable "Reveal cards" to see what this is';
  return div;
}

// Transient floating label on an opponent's tile (e.g. "Raised to 8",
// "Passed") so their action reads at a glance instead of needing the
// (collapsed-by-default) game log. Also flashes the whole tile briefly so
// the eye actually catches it happening, not just the label itself.
function showBubble(username, text, tone) {
  const row = document.querySelector(`.opponent-row[data-username="${CSS.escape(username)}"]`);
  if (!row) return;
  const bubble = document.createElement('div');
  bubble.className = `bubble ${tone || ''}`;
  bubble.textContent = text;
  row.appendChild(bubble);
  requestAnimationFrame(() => bubble.classList.add('show'));

  row.classList.add('flash');
  setTimeout(() => row.classList.remove('flash'), 500);

  setTimeout(() => {
    bubble.classList.remove('show');
    setTimeout(() => bubble.remove(), 250);
  }, 2400);
}

// Points formula mirrors BasePlayer.__calculate_points(): sum of values,
// times the product of multipliers (Passe: -5/×1, Scandale: 0/×0.5,
// Prestige: 0/×2) — see components_module/{disgrace_card,prestige_card}.py.
function computePoints(statusCards) {
  let sum = 0;
  let mult = 1;
  for (const c of statusCards) { sum += c.value; mult *= c.multiplier; }
  return sum * mult;
}

// ---------------------------------------------------------------- state --

let ws = null;
let statusPollTimer = null;
let lastStatus = null;
let pendingJoin = null;
let pendingSpectate = null;
let pendingIdentifyError = null;
let game = null;

// Whether to show opponents' actual won cards/points, or keep them hidden
// behind card-backs — off by default (per user preference: keeping
// opponents' progress hidden makes the game more interesting), persisted
// per-browser so it doesn't reset every game.
let revealCards = localStorage.getItem('hs_reveal_cards') === '1';

function resetGameState(myUsername) {
  game = {
    round: 0,
    card: null,
    maxBid: 0,
    myAuctionBid: 0, // my own cumulative committed bid for the *current* auction only
    turnPlayer: null,
    myUsername,
    myPoints: 0,
    myStatusCards: [],
    selectedBid: new Set(),
    opponents: {}, // username -> {name, statusCards: [], active: true, outOfAuction: false}
  };
}

function seedOpponents(status, myUsername) {
  (status.joined || []).forEach((p) => {
    if (p.username === myUsername) return;
    game.opponents[p.username] = { name: p.name, statusCards: [], active: true, outOfAuction: false };
  });
}

function ensureOpponent(username) {
  if (!game.opponents[username]) {
    game.opponents[username] = { name: username, statusCards: [], active: true, outOfAuction: false };
  }
  return game.opponents[username];
}

// ------------------------------------------------------------------ boot --

document.addEventListener('DOMContentLoaded', () => {
  wireStaticHandlers();
  refreshStatus();
});

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

async function refreshStatus() {
  let status;
  try {
    status = await fetchJSON('/api/status');
  } catch (e) {
    return; // transient network hiccup — next poll (or manual reload) retries
  }
  lastStatus = status;
  renderForStatus(status);
}

function renderForStatus(status) {
  if (!status.exists) {
    stopPolling();
    showScreen('screen-host-setup');
    return;
  }
  if (status.state === 'finished') {
    stopPolling();
    renderFinished(status);
    return;
  }
  if (status.state === 'lobby') {
    if (ws) return; // mid-join; the join flow owns the screen until it resolves
    renderLobby(status);
    startPolling();
    return;
  }
  // starting / in_progress
  if (!ws) {
    stopPolling();
    showScreen('screen-join');
    $('join-form').classList.add('hidden');
    $('join-waiting').classList.add('hidden');
    $('lobby-status').textContent = 'A game is already in progress — you can watch as a spectator.';
  }
}

function startPolling() {
  if (statusPollTimer) return;
  statusPollTimer = setInterval(refreshStatus, 1500);
}
function stopPolling() {
  if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer = null; }
}

function renderLobby(status) {
  showScreen('screen-join');
  $('join-form').classList.remove('hidden');
  $('join-waiting').classList.add('hidden');
  const names = status.joined.map((p) => `${p.name}${p.is_bot ? ' 🤖' : ''}`).join(', ') || 'nobody yet';
  $('lobby-status').textContent = `Seats filled: ${status.joined.length}/${status.seats} — ${names}`;
  if (pendingIdentifyError) {
    showError($('join-error'), pendingIdentifyError);
    pendingIdentifyError = null;
  }
}

function renderFinished(status) {
  showScreen('screen-finished');
  const standings = (status.final_standings || []).slice().sort((a, b) => b.points - a.points);
  const winners = new Set(status.winners || []);
  if (winners.size === 1) {
    $('finished-headline').textContent = `🏆 ${[...winners][0]} wins!`;
  } else if (winners.size > 1) {
    $('finished-headline').textContent = `🤝 Tie: ${[...winners].join(', ')}`;
  } else {
    $('finished-headline').textContent = 'Game over';
  }
  const rows = standings.map((s) => `
    <div class="standing-row ${winners.has(s.username) ? 'winner' : ''} ${s.active === false ? 'inactive' : ''}">
      <span class="name">${s.username}${winners.has(s.username) ? ' 🏆' : ''}${s.active === false ? ' (left)' : ''}</span>
      <span>Points: ${s.points}</span>
      <span>Money left: ${s.money_left}</span>
    </div>`).join('');
  $('standings-table').innerHTML = rows || '<p class="muted">No standings available.</p>';
}

// ------------------------------------------------------------- host flow --

function wireStaticHandlers() {
  $('btn-create-game').addEventListener('click', onCreateGame);
  $('btn-join').addEventListener('click', onJoin);
  $('btn-spectate-link').addEventListener('click', () => showScreen('screen-spectate-join'));
  $('btn-back-to-join').addEventListener('click', () => { showScreen('screen-join'); refreshStatus(); });
  $('btn-spectate-join').addEventListener('click', onSpectateJoin);
  $('btn-new-game').addEventListener('click', () => showScreen('screen-host-setup'));
  $('btn-place-bid').addEventListener('click', onPlaceBid);
  $('btn-pass').addEventListener('click', onPass);
  $('btn-quit').addEventListener('click', onQuit);
  $('btn-spec-chat-send').addEventListener('click', onSpecChatSend);
  $('spec-chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') onSpecChatSend(); });

  $('reveal-cards-toggle').checked = revealCards;
  $('spec-reveal-cards-toggle').checked = revealCards;
  const onRevealToggle = (e) => {
    revealCards = e.target.checked;
    localStorage.setItem('hs_reveal_cards', revealCards ? '1' : '0');
    $('reveal-cards-toggle').checked = revealCards;
    $('spec-reveal-cards-toggle').checked = revealCards;
    if (game) { renderOpponents(false); renderOpponents(true); }
  };
  $('reveal-cards-toggle').addEventListener('change', onRevealToggle);
  $('spec-reveal-cards-toggle').addEventListener('change', onRevealToggle);

  window.addEventListener('beforeunload', (e) => {
    if (ws && ws.readyState === WebSocket.OPEN && game && game.round > 0) {
      e.preventDefault();
      e.returnValue = 'Leaving now drops you from the game — there is no reconnect.';
    }
  });
}

async function onCreateGame() {
  hide($('host-error'));
  const seats = parseInt($('host-seats').value, 10);
  const counts = {
    pass: parseInt($('bot-pass').value || '0', 10),
    greedy: parseInt($('bot-greedy').value || '0', 10),
    capped: parseInt($('bot-capped').value || '0', 10),
  };
  const botMix = [];
  for (const [type, n] of Object.entries(counts)) for (let i = 0; i < n; i += 1) botMix.push(type);

  const seedRaw = $('host-seed').value;
  const body = {
    seats,
    bot_mix: botMix,
    seed: seedRaw ? parseInt(seedRaw, 10) : null,
    bot_think_time: parseFloat($('host-think-time').value || '1'),
  };
  try {
    const status = await fetchJSON('/api/create_game', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    lastStatus = status;
    renderLobby(status);
    startPolling();
  } catch (e) {
    showError($('host-error'), e.message);
  }
}

// ------------------------------------------------------------- join flow --

function respondIdentify(socket, pending, msg) {
  const wantsUsername = /username/i.test(msg.prompt);
  const answer = wantsUsername ? pending.username : pending.name;
  socket.send(JSON.stringify({ message_type: 'IDENTIFY_ACK', prompt: answer }));
}

function onJoin() {
  hide($('join-error'));
  const username = $('join-username').value.trim();
  const name = $('join-name').value.trim() || username;
  if (!username) { showError($('join-error'), 'Username is required.'); return; }
  pendingJoin = { username, name };
  stopPolling();
  resetGameState(username);
  if (lastStatus) seedOpponents(lastStatus, username);
  connectPlayerSocket();
}

function connectPlayerSocket() {
  ws = new WebSocket(wsUrl('/ws'));
  ws.onmessage = (evt) => handlePlayerMessage(JSON.parse(evt.data));
  ws.onclose = () => { ws = null; refreshStatus(); };
  setBadge('connecting…');
}

function handlePlayerMessage(msg) {
  switch (msg.message_type) {
    case 'IDENTIFY':
      respondIdentify(ws, pendingJoin, msg);
      break;
    case 'IDENTIFY_ERROR':
      pendingIdentifyError = msg.prompt;
      ws.close();
      break;
    case 'IDENTIFY_SUCCESS':
      $('join-form').classList.add('hidden');
      $('join-waiting').classList.remove('hidden');
      setBadge(`playing as ${game.myUsername}`);
      break;
    default:
      applyGameMessage(msg, false);
  }
}

// --------------------------------------------------------- spectate flow --

function onSpectateJoin() {
  hide($('spectate-error'));
  const name = $('spectate-name').value.trim();
  const username = $('spectate-username').value.trim();
  if (!name || !username) { showError($('spectate-error'), 'Both fields are required.'); return; }
  pendingSpectate = { username, name };
  stopPolling();
  resetGameState(null);
  fetchJSON('/api/status').then((status) => seedOpponents(status, null)).catch(() => {});
  connectSpectatorSocket();
}

function connectSpectatorSocket() {
  ws = new WebSocket(wsUrl('/ws_spectate'));
  ws.onmessage = (evt) => handleSpectatorMessage(JSON.parse(evt.data));
  ws.onclose = () => { ws = null; };
  showScreen('screen-spectate');
  setBadge('spectating');
}

function handleSpectatorMessage(msg) {
  switch (msg.message_type) {
    case 'IDENTIFY':
      respondIdentify(ws, pendingSpectate, msg);
      break;
    case 'IDENTIFY_ERROR':
      showError($('spectate-error'), msg.prompt);
      ws.close();
      showScreen('screen-spectate-join');
      break;
    case 'IDENTIFY_SUCCESS':
      break;
    default:
      applyGameMessage(msg, true);
  }
}

function onSpecChatSend() {
  const input = $('spec-chat-input');
  const text = input.value.trim();
  if (!text || !ws) return;
  ws.send(JSON.stringify({ message_type: 'CHAT', prompt: text }));
  input.value = '';
}

// --------------------------------------------------------- game reducer --

function ensureGameScreenVisible(isSpectator) {
  const id = isSpectator ? 'screen-spectate' : 'screen-game';
  if ($(id).classList.contains('hidden')) showScreen(id);
}

function logLine(text, isSpectator) {
  if (!text) return;
  const el = $(isSpectator ? 'spec-game-log' : 'game-log');
  const p = document.createElement('div');
  p.textContent = text;
  el.appendChild(p);
  el.scrollTop = el.scrollHeight;
}

// gameplay.py broadcasts a plain-text GLOBAL_EVENT narration line right next
// to most of the structured events this UI already renders from
// AUCTION_UPDATE (turn/bid/pass/fold/quit/auction_start) and AUCTION_RESULT
// (the win announcement) — see gameplay.py's _broadcast_auction_update. Skip
// re-logging those specific plain-text lines so the log doesn't show every
// event twice; every other GLOBAL_EVENT (countdown, green card, faux pas,
// final standings/winner) has no structured counterpart and is still logged.
// Coupled to the exact wording/emoji gameplay.py uses today — if that prose
// changes, update this alongside it.
const DUPLICATE_NARRATION_PATTERNS = [
  /^Auctioning:/,
  /^💀 Disgrace Auction started for:/,
  /^💰 /,
  /^⚪ /,
  /^❌ /,
  /^💢 /,
  /wins the auction for/,
];
function isDuplicateOfStructuredEvent(text) {
  return DUPLICATE_NARRATION_PATTERNS.some((re) => re.test(text.trim()));
}

function applyGameMessage(msg, isSpectator) {
  ensureGameScreenVisible(isSpectator);
  switch (msg.message_type) {
    case 'GLOBAL_EVENT': {
      const d = msg.data;
      if (d && d.event === 'faux_pas_discard') {
        if (d.player !== game.myUsername) {
          const o = ensureOpponent(d.player);
          o.statusCards = o.statusCards.filter((c) => c.value !== d.discarded_value);
          showBubble(d.player, revealCards ? `Discarded ${d.discarded_value}` : 'Discarded a painting', 'discard');
        }
        renderOpponents(isSpectator);
      }
      if (msg.prompt && !isDuplicateOfStructuredEvent(msg.prompt)) logLine(msg.prompt.trim(), isSpectator);
      break;
    }
    case 'AUCTION_UPDATE':
      applyAuctionUpdate(msg, isSpectator);
      break;
    case 'AUCTION_RESULT':
      applyAuctionResult(msg, isSpectator);
      break;
    case 'PLAYER_STATE':
      if (!isSpectator) applyPlayerState(msg);
      break;
    case 'PLAYER_MOVE':
      if (!isSpectator) applyPlayerMove(msg);
      break;
    case 'INPUT_ERROR':
      if (!isSpectator) showError($('move-error'), msg.prompt);
      break;
    case 'CHAT':
      if (isSpectator) {
        const el = $('spec-chat-log');
        const p = document.createElement('div');
        p.textContent = msg.prompt;
        el.appendChild(p);
        el.scrollTop = el.scrollHeight;
      }
      break;
    default:
      break; // GLOBAL_MOVE_INFO, PLAYER_INFO, PLAYER_MOVE_TIMER: superseded by the structured messages above
  }
}

function applyAuctionUpdate(msg, isSpectator) {
  const d = msg.data;
  game.round = d.round_number;
  game.card = d.card;
  if (typeof d.max_bid === 'number') game.maxBid = d.max_bid;

  if (d.kind === 'auction_start') {
    game.maxBid = 0;
    game.myAuctionBid = 0;
    game.turnPlayer = d.starting_player;
    // Everyone's back in for the new auction — clear last round's greyed-out state.
    Object.values(game.opponents).forEach((o) => { o.outOfAuction = false; });
    logLine(`🃏 Auction #${d.round_number}: ${describeCard(d.card)}`, isSpectator);
  } else if (d.kind === 'turn_start') {
    game.turnPlayer = d.player;
  } else if (d.kind === 'bid') {
    if (d.player === game.myUsername) {
      game.myAuctionBid = d.max_bid; // this event's max_bid is the bidder's own new cumulative total
      updateBidStatus();
    } else {
      showBubble(d.player, `Raised to ${d.max_bid}`, 'bid');
    }
    logLine(`💰 ${d.player} raised to ${d.max_bid}`, isSpectator);
  } else if (d.kind === 'pass' || d.kind === 'fold') {
    if (d.player !== game.myUsername) {
      showBubble(d.player, 'Passed', 'pass');
      if (game.opponents[d.player]) game.opponents[d.player].outOfAuction = true;
    }
    logLine(`⚪ ${d.player} passed`, isSpectator);
  } else if (d.kind === 'quit') {
    if (d.player !== game.myUsername) showBubble(d.player, 'Quit', 'quit');
    if (game.opponents[d.player]) { game.opponents[d.player].active = false; game.opponents[d.player].outOfAuction = true; }
    logLine(`❌ ${d.player} quit`, isSpectator);
  }
  renderAuctionPanel(isSpectator);
}

function applyAuctionResult(msg, isSpectator) {
  const d = msg.data;
  if (d.recipient) {
    if (d.recipient !== game.myUsername) {
      ensureOpponent(d.recipient).statusCards.push(d.card);
    }
    const spent = (d.money_spent && d.money_spent[d.recipient]) || 0;
    logLine(`🏆 ${d.recipient} won ${describeCard(d.card)} for ${spent}`, isSpectator);
  } else {
    logLine(`⚠️ Nobody took ${describeCard(d.card)}`, isSpectator);
  }
  renderOpponents(isSpectator);
}

function applyPlayerState(msg) {
  const d = msg.data;
  game.myPoints = d.points;
  game.myStatusCards = d.status_cards;
  renderMyPanel();
}

function applyPlayerMove(msg) {
  $('move-error').classList.add('hidden');
  $('move-panel').classList.remove('hidden', 'pending');
  const bidControls = $('bid-controls');
  const discardControls = $('discard-controls');
  if (msg.move_type === 'discard_painting') {
    bidControls.classList.add('hidden');
    discardControls.classList.remove('hidden');
    renderPaintingChoices(msg.constraints.allowed_paintings);
  } else {
    discardControls.classList.add('hidden');
    bidControls.classList.remove('hidden');
    game.selectedBid = new Set();
    renderMoneyChips(msg.constraints.allowed_money_cards);
    updateBidStatus();
  }
}

// Marks the move panel as "acted on, waiting for the table" — greyed out and
// non-interactive but still visible (so you can see what you just did),
// rather than disappearing entirely between your turns.
function setMovePending() {
  $('move-panel').classList.add('pending');
}

// ------------------------------------------------------------- rendering --

function renderAuctionPanel(isSpectator) {
  const prefix = isSpectator ? 'spec-' : '';
  $(`${prefix}round-label`).innerHTML = game.round ? `<span class="suit-icon">🂠</span> Auction <strong>#${game.round}</strong>` : '';
  $(`${prefix}turn-label`).innerHTML = game.turnPlayer
    ? `<span class="turn-dot"></span>${game.turnPlayer}'s turn`
    : '';
  $(`${prefix}max-bid`).textContent = game.maxBid || 0;
  const cardContainer = $(`${prefix}auction-card`);
  cardContainer.innerHTML = '';
  if (game.card) cardContainer.appendChild(cardEl(game.card, true));
  renderOpponents(isSpectator);
}

function renderOpponents(isSpectator) {
  if (!game) return;
  const container = $(isSpectator ? 'spec-players-list' : 'opponents-list');
  container.innerHTML = '';
  Object.entries(game.opponents).forEach(([username, o]) => {
    const row = document.createElement('div');
    row.dataset.username = username;
    const isCurrentTurn = game.turnPlayer === username;
    const classes = ['opponent-row'];
    if (o.active === false) classes.push('inactive');
    if (o.outOfAuction) classes.push('out-of-auction');
    else if (!isCurrentTurn) classes.push('waiting'); // still in this auction, just not acting right now
    if (isCurrentTurn) classes.push('current-turn');
    row.className = classes.join(' ');

    const header = document.createElement('div');
    header.className = 'opponent-header';
    const ptsLabel = revealCards ? `Points: ${computePoints(o.statusCards)}` : `${o.statusCards.length} card${o.statusCards.length === 1 ? '' : 's'}`;
    header.innerHTML = `<span class="name">${o.name}${o.active === false ? ' (out)' : ''}</span><span class="pts">${ptsLabel}</span>`;
    row.appendChild(header);

    const chips = document.createElement('div');
    chips.className = 'chip-row small';
    o.statusCards.forEach((c) => chips.appendChild(revealCards ? cardEl(c) : cardBackEl()));
    row.appendChild(chips);

    container.appendChild(row);
  });
}

function renderMyPanel() {
  $('my-username-label').textContent = game.myUsername || '';
  $('my-points').textContent = game.myPoints;
  const chips = $('my-status-cards');
  chips.innerHTML = '';
  game.myStatusCards.forEach((c) => chips.appendChild(cardEl(c)));
}

function renderMoneyChips(values) {
  const row = $('my-money-cards');
  row.innerHTML = '';
  values.slice().sort((a, b) => a - b).forEach((value) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip money';
    btn.textContent = value;
    btn.addEventListener('click', () => {
      if (game.selectedBid.has(value)) game.selectedBid.delete(value); else game.selectedBid.add(value);
      btn.classList.toggle('selected');
      updateSelectedBidTotal();
    });
    row.appendChild(btn);
  });
  updateSelectedBidTotal();
}

// Money committed to an auction stays on the table for its whole duration
// (BasePlayer.place_bid() adds to current_bid_value across turns, it never
// replaces it) — so "selected" chips here are cards being ADDED on top of
// whatever you already committed earlier this same auction, not your new
// total outright. Surfacing both numbers is what point 3 of the user's
// feedback asked for: it's otherwise hard to tell how much more you need
// without digging through the log.
function updateBidStatus() {
  $('my-current-bid').textContent = game.myAuctionBid;
  $('bid-need-more').textContent = game.maxBid > 0 ? `(add more than ${game.maxBid - game.myAuctionBid} to raise)` : '';
  updateSelectedBidTotal();
}

function updateSelectedBidTotal() {
  const addingTotal = [...game.selectedBid].reduce((a, b) => a + b, 0);
  $('selected-bid').textContent = addingTotal;
  $('new-total-bid').textContent = game.myAuctionBid + addingTotal;
}

function renderPaintingChoices(values) {
  const row = $('my-paintings');
  row.innerHTML = '';
  values.forEach((value) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip painting';
    btn.textContent = value;
    btn.addEventListener('click', () => {
      ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: String(value) }));
      setMovePending();
    });
    row.appendChild(btn);
  });
}

// ------------------------------------------------------------- controls --

function onPlaceBid() {
  const values = [...game.selectedBid];
  if (values.length === 0) { showError($('move-error'), 'Select at least one money card.'); return; }
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: JSON.stringify(values) }));
  setMovePending();
}

function onPass() {
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: 'pass' }));
  setMovePending();
}

function onQuit() {
  if (!confirm('Quit the game? This cannot be undone.')) return;
  ws.send(JSON.stringify({ message_type: 'RESPONSE', prompt: 'quit' }));
  setMovePending();
}
