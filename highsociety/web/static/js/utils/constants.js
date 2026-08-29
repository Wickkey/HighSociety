// Shared constants with more than one consumer module. Single-consumer data
// (e.g. the achievements catalog) lives next to its one renderer instead of
// here -- see account/account.js.

// Shared by the move-timer badge and each opponent's own timer badge --
// a line-art clock instead of the "⏰" emoji, matching the rest of the
// app's icon language (stroke-based SVG, not a font glyph -- see the
// profile chip / sidebar for the same reasoning).
export const CLOCK_ICON_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
  + '<path d="M12 7.5V12l3 2"/></svg>';

// No identity yet to act on (screen-login) or the table needs the width
// (screen-game, already tight -- see the move-panel sizing history in
// style.css) -- everywhere else the nav rail is a fixed part of the layout.
export const SIDEBAR_HIDDEN_SCREENS = new Set(['screen-login', 'screen-game']);

// The "Games played / Players" sticky footer only makes sense on the
// top-level, sidebar-navigable screens someone browses between games --
// not once they're inside an actual room (waiting room, live game,
// spectating, finished) where it would just be clutter over gameplay.
// An allow-list (rather than SIDEBAR_HIDDEN_SCREENS' deny-list) since
// there are more "hide" screens than "show" ones.
// 'screen-host-setup' is deliberately NOT here despite showing the tile
// picker sometimes: every one of its own callers already pairs
// showScreen('screen-host-setup') with either showHomeTile() (a Join/
// Host/Rules sub-panel, which immediately hides this footer again -- see
// its own hide($('home-global-stats'))) or showHomeTiles() (the tile
// picker itself, which already calls loadHomeGlobalStats() directly).
// Including it here made every single one of those navigations fetch
// global stats *twice* -- once from this Set membership, once from
// showHomeTiles()'s own explicit call -- a real, reported bug (visible
// as the home screen's stats/Recent-Games area appearing to load more
// than once on every "back to home" navigation).
export const GLOBAL_STATS_FOOTER_SCREENS = new Set([
  'screen-matchmaking', 'screen-leaderboard',
  'screen-account', 'screen-achievements', 'screen-game-history',
]);

const TOAST_DURATION_MS = 1500; // long enough to actually read before it clears
// "X bought Y for Z" / "X is stuck with Y" packs in more to actually read
// (who, what card, how much) than a routine bid/pass update — this was
// specifically called out as feeling rushed for someone new to the game,
// so it gets noticeably longer before the next auction's own toast can
// claim the slot.
const RESULT_TOAST_DURATION_MS = 3000;
export { TOAST_DURATION_MS, RESULT_TOAST_DURATION_MS };

// A single persisted {username, name} — the same identity used across every
// room this browser hosts/joins/spectates. See auth/profile.js.
export const PROFILE_STORAGE_KEY = 'hs_profile';

// Bidding-rules text for the ⓘ button next to the auction card, keyed by
// card.type — Painting/PrestigeCard are normal auctions (highest bidder wins
// and pays), FauxPas/Passe/Scandale are "disgrace" auctions with the exact
// opposite dynamic (first player to PASS takes the card; everyone else's
// raised money is simply lost). This is the single most confusing rule for
// new players, hence spelling it out per card type rather than assuming it's
// obvious from the card's face value alone.
export const CARD_INFO_TEXT = {
  Painting: 'Normal auction. Highest bidder wins and pays their bid. Worth its printed value in points.',
  PrestigeCard: 'Normal auction. Highest bidder wins and pays their bid. Doubles your entire final score. High stakes!',
  FauxPas: "Disgrace auction: opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Taking it means you must immediately discard a Painting you own (or your next one, if you don't have one yet).",
  Passe: 'Disgrace auction: opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Costs you 5 points.',
  Scandale: 'Disgrace auction: opposite rules! The FIRST player to pass takes this card, and everyone who raised loses that money for nothing. Halves your entire final score.',
};
