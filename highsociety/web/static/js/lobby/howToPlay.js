// The How to Play screen's three tabs: Video Tutorial, Practice Game, Read
// the Rules. Opens on the tab named in the URL (/rules#video, #practice,
// #read -- so a shared link lands on the right one), else the one this
// browser used last, else the video for a first visit.
import { $ } from '../utils/dom.js';

const TABS = ['video', 'practice', 'read'];
const LAST_TAB_KEY = 'hs_htp_tab';
const VIDEO_ID = 'WKGdz9DevIY';

function rememberedTab() {
  try {
    return localStorage.getItem(LAST_TAB_KEY);
  } catch (e) {
    return null; // private window / blocked storage -- just use the default
  }
}

export function selectHowToPlayTab(tab, { remember = true } = {}) {
  if (!TABS.includes(tab)) tab = 'video';
  if (tab !== 'video') stopHowToPlayVideo();
  TABS.forEach((t) => {
    const on = t === tab;
    const btn = $(`htp-tab-${t}`);
    btn.classList.toggle('selected', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
    btn.tabIndex = on ? 0 : -1;
    $(`htp-pane-${t}`).classList.toggle('hidden', !on);
  });
  if (location.pathname === '/rules') history.replaceState(null, '', `/rules#${tab}`);
  if (remember) {
    try { localStorage.setItem(LAST_TAB_KEY, tab); } catch (e) { /* see rememberedTab */ }
  }
}

// Called as the screen opens (lobby.js's showHomeTile), with the hash read
// before setScreenPath rewrote the URL to plain /rules.
export function showHowToPlay(hash) {
  const fromUrl = (hash || '').replace('#', '');
  const tab = TABS.includes(fromUrl) ? fromUrl : (rememberedTab() || 'video');
  selectHowToPlayTab(tab, { remember: TABS.includes(fromUrl) });
}

export function onHowToPlayTabClick(e) {
  const btn = e.target.closest('[data-htp-tab]');
  if (btn) selectHowToPlayTab(btn.dataset.htpTab);
}

// Left/right arrows move between tabs, the usual tablist keyboard pattern.
export function onHowToPlayTabKeydown(e) {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
  const current = TABS.findIndex((t) => $(`htp-tab-${t}`).classList.contains('selected'));
  const next = TABS[(current + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length];
  selectHowToPlayTab(next);
  $(`htp-tab-${next}`).focus();
}

// "Try a practice game →" style links inside a tab.
export function onHowToPlayGotoClick(e) {
  const link = e.target.closest('[data-htp-goto]');
  if (!link) return;
  selectHowToPlayTab(link.dataset.htpGoto);
  $(`htp-tab-${link.dataset.htpGoto}`).focus();
}

let _facade = null; // the thumbnail button, put back by stopHowToPlayVideo

// Swaps the thumbnail for YouTube's player, already playing. Until this
// click nothing from YouTube loads but the thumbnail image.
export function playHowToPlayVideo() {
  _facade = $('btn-htp-video-play');
  const frame = document.createElement('iframe');
  frame.src = `https://www.youtube-nocookie.com/embed/${VIDEO_ID}?autoplay=1&rel=0`;
  frame.title = 'How to Play High Society';
  frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
  frame.referrerPolicy = 'strict-origin-when-cross-origin';
  frame.allowFullscreen = true;
  $('htp-video').replaceChildren(frame);
}

// Back to the thumbnail, which also stops the sound -- a hidden player
// would otherwise keep playing after switching tabs or leaving the screen.
export function stopHowToPlayVideo() {
  if (_facade && !_facade.isConnected) $('htp-video').replaceChildren(_facade);
}
