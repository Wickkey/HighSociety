// Entry point: wires every static DOM event listener and boots the app on
// DOMContentLoaded. Owns nothing itself -- every handler it wires lives in
// its own feature module; this file only imports and attaches them.
import { $, showScreen } from './utils/dom.js';
import { resolveConfirmDialog, openProfileModal, closeProfileModal } from './ui/modals.js';
import { onSpecChatSend, onPlayerChatSend } from './ui/chat.js';
import {
  onContinueAsGuest, onShuffleUsername, onLoginUsernameContinue, initGoogleSignIn, proceedPastLogin,
} from './auth/login.js';
import {
  loadProfile, renderProfileChip, onProfileChipClick, onLogout, closeProfilePopover,
} from './auth/profile.js';
import { showAccountScreen, onAccountSaveClick } from './account/account.js';
import {
  showHomeTile, showHomeTiles, onHomeLinkClick, navigateFromSidebar, onCreateGame, onJoinByCode,
  onCopyRoomLink, onJoin, onSpectateJoin, onChangeJoinIdentity, onChangeSpectateIdentity,
  startRoomsPolling, currentRoomCode, isActivelyPlayingLiveGame,
  applySpectateIdentityDefaults, refreshStatus, leaveToHome, setCurrentRoomCode,
} from './lobby/lobby.js';
import { onAddBot } from './lobby/playerList.js';
import { onPlayClick, onFindMatch, onMatchmakingCancel, onMatchmakingAddBots } from './lobby/matchmaking.js';
import {
  onRequestRematchClick, onCancelRematchForm, onSendRematchRequest, onAcceptRematch, onDeclineRematch,
  onStandingsTableClick,
} from './lobby/rematch.js';
import { onPlaceBid, onPass, onResign, onDiscardPainting, onQuickReactionClick } from './game/gameActions.js';

// Debug-only bridge, not part of the app's real API surface: lets a
// browser console (or a Playwright page.evaluate, as used throughout this
// project's own live-verification passes) reach internal state the same
// way bare globals used to, now that real ES modules make everything
// module-scoped by default.
import * as gameStateModule from './game/gameState.js';
import * as gameEventsModule from './game/gameEvents.js';
import * as gameRendererModule from './game/gameRenderer.js';
window.__hsDebug = {
  get game() { return gameStateModule.game; },
  resetGameState: gameStateModule.resetGameState,
  applyPlayerMove: gameEventsModule.applyGameMessage,
  showReactionBubble: gameEventsModule.showReactionBubble,
  renderOpponents: gameRendererModule.renderOpponents,
};

function wireStaticHandlers() {
  $('confirm-modal-cancel').addEventListener('click', () => resolveConfirmDialog(false));
  $('confirm-modal-confirm').addEventListener('click', () => resolveConfirmDialog(true));
  $('btn-continue-guest').addEventListener('click', onContinueAsGuest);
  $('btn-shuffle-username').addEventListener('click', onShuffleUsername);
  $('btn-login-continue').addEventListener('click', onLoginUsernameContinue);
  $('login-username').addEventListener('keydown', (e) => { if (e.key === 'Enter') onLoginUsernameContinue(); });
  document.querySelectorAll('.home-tile').forEach((btn) => {
    btn.addEventListener('click', () => showHomeTile(btn.dataset.homeTarget));
  });
  $('sidebar-play').addEventListener('click', () => navigateFromSidebar(onPlayClick));
  $('sidebar-join').addEventListener('click', () => navigateFromSidebar(() => {
    showScreen('screen-host-setup'); showHomeTile('join'); startRoomsPolling();
  }));
  $('sidebar-host').addEventListener('click', () => navigateFromSidebar(() => {
    showScreen('screen-host-setup'); showHomeTile('host');
  }));
  $('sidebar-rules').addEventListener('click', () => navigateFromSidebar(() => {
    showScreen('screen-host-setup'); showHomeTile('rules');
  }));
  $('sidebar-account').addEventListener('click', () => navigateFromSidebar(showAccountScreen));
  document.querySelectorAll('.home-back').forEach((btn) => {
    btn.addEventListener('click', showHomeTiles);
  });
  $('btn-leave-lobby-back').addEventListener('click', onHomeLinkClick);
  $('btn-create-game').addEventListener('click', onCreateGame);
  $('btn-join-by-code').addEventListener('click', onJoinByCode);
  $('btn-copy-room-link').addEventListener('click', onCopyRoomLink);
  $('room-link-input').addEventListener('focus', (e) => e.target.select());
  $('room-link-input').addEventListener('click', (e) => e.target.select());
  $('btn-add-bot').addEventListener('click', onAddBot);
  $('btn-join').addEventListener('click', onJoin);
  $('btn-spectate-link').addEventListener('click', () => {
    applySpectateIdentityDefaults();
    showScreen('screen-spectate-join');
  });
  $('btn-back-to-join').addEventListener('click', () => {
    showScreen('screen-join');
    refreshStatus();
  });
  $('btn-spectate-join').addEventListener('click', onSpectateJoin);
  $('btn-new-game').addEventListener('click', () => leaveToHome());
  $('connection-badge').addEventListener('click', onProfileChipClick);
  $('btn-open-account').addEventListener('click', () => { closeProfilePopover(); showAccountScreen(); });
  $('btn-account-back').addEventListener('click', () => navigateFromSidebar(() => {
    showScreen('screen-host-setup'); showHomeTiles(); startRoomsPolling();
  }));
  $('btn-account-save').addEventListener('click', onAccountSaveClick);
  $('btn-account-logout').addEventListener('click', onLogout);
  $('btn-find-match').addEventListener('click', onFindMatch);
  $('btn-matchmaking-cancel').addEventListener('click', onMatchmakingCancel);
  $('btn-matchmaking-back').addEventListener('click', onMatchmakingCancel);
  $('btn-matchmaking-add-bots').addEventListener('click', onMatchmakingAddBots);
  $('btn-logout').addEventListener('click', onLogout);
  // Standard popover UX: a click anywhere outside the chip/popover itself
  // closes it, same as a browser's own menus.
  document.addEventListener('click', (e) => {
    if (!$('profile-chip-wrap').contains(e.target)) closeProfilePopover();
  });
  $('btn-change-join-identity').addEventListener('click', onChangeJoinIdentity);
  $('btn-change-spectate-identity').addEventListener('click', onChangeSpectateIdentity);
  $('home-link').addEventListener('click', onHomeLinkClick);
  $('btn-stop-watching').addEventListener('click', onHomeLinkClick);
  $('home-link').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onHomeLinkClick(); }
  });
  $('btn-place-bid').addEventListener('click', onPlaceBid);
  $('btn-pass').addEventListener('click', onPass);
  $('btn-resign').addEventListener('click', onResign);
  $('btn-discard-painting').addEventListener('click', onDiscardPainting);
  $('btn-spec-chat-send').addEventListener('click', onSpecChatSend);
  $('spec-chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') onSpecChatSend(); });
  $('spec-chat-target-toggle').addEventListener('change', (e) => {
    $('spec-chat-input').placeholder = e.target.checked ? 'Message spectators only…' : 'Message everyone…';
  });
  $('btn-player-chat-send').addEventListener('click', onPlayerChatSend);
  $('player-chat-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') onPlayerChatSend(); });
  document.querySelectorAll('.quick-reaction-btn').forEach((btn) => {
    btn.addEventListener('click', () => onQuickReactionClick(btn.dataset.emoji));
  });
  $('standings-table').addEventListener('click', onStandingsTableClick);
  $('profile-view-close').addEventListener('click', closeProfileModal);
  $('profile-view-modal').addEventListener('click', (e) => {
    if (e.target.id === 'profile-view-modal') closeProfileModal();
  });
  $('btn-request-rematch').addEventListener('click', onRequestRematchClick);
  $('btn-cancel-rematch-form').addEventListener('click', onCancelRematchForm);
  $('btn-send-rematch-request').addEventListener('click', onSendRematchRequest);
  $('btn-accept-rematch').addEventListener('click', onAcceptRematch);
  $('btn-decline-rematch').addEventListener('click', onDeclineRematch);

  window.addEventListener('beforeunload', (e) => {
    if (isActivelyPlayingLiveGame()) {
      e.preventDefault();
      e.returnValue = 'Leaving now drops you from the game. There is no reconnect.';
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  wireStaticHandlers();
  renderProfileChip();
  // Covers the case where the GIS script (see index.html) finished
  // loading and already ran *before* this script did -- its own onload
  // already fired and no-op'd since initGoogleSignIn didn't exist yet at
  // that moment. window.google.accounts already being present here means
  // it's safe to render the button right now instead of waiting for an
  // onload that already happened. (The reverse ordering -- this running
  // first -- is already handled: initGoogleSignIn's own early-return
  // guard no-ops when window.google isn't ready yet, and the GIS
  // script's onload calls it again once it actually loads.)
  initGoogleSignIn();
  setCurrentRoomCode(new URLSearchParams(location.search).get('room'));
  // A returning visitor who already has a saved profile (guest or Google)
  // skips straight past #screen-login -- only a genuinely new visitor, or
  // someone who explicitly logged out, sees it.
  if (loadProfile()) {
    proceedPastLogin();
  } else {
    showScreen('screen-login');
  }
});
