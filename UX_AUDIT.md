# UX Audit — web frontend

A pass through every screen (code read + live click-through in a real browser, desktop and
mobile viewports) looking for friction, dead ends, and inconsistencies. Everything below was
either observed live or confirmed directly in the source — nothing here is speculative.

Ordered roughly by impact, not by effort to fix.

---

## 1. Spectators have no way to leave

**Where:** `#screen-spectate` (`index.html` ~L407-477)

A player gets an explicit red **Resign** button in their "You" panel. A spectator gets nothing —
no "Stop watching" / "Leave" button anywhere on the screen. The only way out is clicking the
small "High Society" wordmark in the header, which happens to double as a home link
(`app.js`'s `onHomeLinkClick`). That's not discoverable as an exit — nothing about it visually
reads as "leave," and a first-time visitor watching a game has no reason to think the logo is
clickable for that purpose.

**Fix:** add a "Stop watching" / "Back to home" button to the spectator screen, visually
consistent with where Resign sits for players.

---

## 2. Players can't see who's watching

**Where:** `#screen-game`'s side column (`index.html` ~L386-403) vs. `#screen-spectate`'s side
column (~L467-475)

Spectators get a full "Players" panel listing everyone at the table. Players get nothing back —
there is no spectator count or list anywhere in the player view, and nothing in `app.js` ever
tracks or renders one (confirmed: no `spectator_count`/spectator-list handling exists on the
player side at all). If five people are watching a private game, none of the players ever know.

**Fix:** at minimum, a spectator count next to "Opponents" (`Opponents · 3 watching`). A full
list is a bigger lift (would need a new broadcast — spectator joins/leaves aren't currently
pushed to players) but even the count would close most of the gap.

---

## 3. Bot-type dropdown and "add bot" button don't visually read as one control

**Where:** `#join-waiting .button-row` (`index.html` L273-280)

```
[Medium ▾]  [Fill an empty seat with a bot]
```

The dropdown has no label (`<select id="waiting-bot-type">` — just shows "Easy/Medium/Hard"
with no adjacent text like "Difficulty:"), and it sits as a separate, disconnected control next
to a large bold button. Visually there's nothing tying the two together — a new user has to
infer that the dropdown modifies what the button does, rather than being, say, a leftover
control for something else. This is the exact spot flagged directly: "easy, med, hard dropdown
is on left, add bot is on right (counterintuitive)."

**Fix:** either make it read as one sentence — `Fill an empty seat with a [Medium ▾] bot` — or
group them visually (shared border/background) so the pairing is obvious at a glance. The same
pattern repeats identically in the rematch form's bot-mix section, so worth fixing once and
reusing.

---

## 4. Floating GitHub badge overlaps game controls on mobile

**Where:** `.github-link` (`style.css` L83-98) — `position: fixed; right: 1.5rem; bottom: 1.5rem;
z-index: 150`, present on every screen.

Confirmed live at a 375px viewport: the circular GitHub icon sits directly on top of the
"Place Bid" button during an active turn, and separately on top of the bot-count input on the
host form. It's a fixed, always-on-top element with no awareness of what's underneath it on a
narrow screen — a mistap here does nothing catastrophic (it just opens GitHub in a new tab), but
it's actively covering the primary action button at the exact moment a mobile player needs to
tap it.

**Fix:** hide or reposition it below `~500px` width (a `@media` rule), or move it into the
header instead of floating.

---

## 5. Seat count flashes wrong for ~1.5s right after joining

**Where:** `startWaitingRoomPolling()` (`app.js` ~L766-782)

```js
function startWaitingRoomPolling() {
  if (waitingRoomPollTimer) return;
  waitingRoomPollTimer = setInterval(async () => { ... }, 1500);
}
```

`setInterval` doesn't fire immediately — its first tick is 1.5s out. Reproduced live: right
after joining, the screen briefly shows **"Seats filled: 0/3 — nobody yet"** directly next to
**"✅ You're in! Waiting for the rest of the table…"** — visibly contradictory for that first
1.5s window, before the poller's first tick corrects it.

The sibling poller for the public-rooms list already gets this right —
`startRoomsPolling()` (`app.js` ~L667-670) calls `refreshRoomsList()` once *before* starting the
interval:
```js
function startRoomsPolling() {
  if (roomsPollTimer) return;
  refreshRoomsList();
  roomsPollTimer = setInterval(refreshRoomsList, 2000);
}
```

**Fix:** make `startWaitingRoomPolling()` match that pattern — call the update function once,
then start the interval.

---

## 6. Rule explanation duplicated in two places, and the two copies disagree

**Where:** `index.html` L131 (home screen's "Game Rules" tile) vs. L230 (join screen's own
"How to play" — same content, separate `<details>`)

The disgrace-auction explanation was rewritten in one copy but not the other:

- Home "Game Rules": *"the first player to pass gets stuck with the card **and gets their money
  back**. Everyone else forfeits their raised money."*
- Join screen: *"the first player to pass gets stuck with the card. Everyone else forfeits their
  raised money for nothing — so you want to bail early, but bailing is what sticks you with it."*

The join-screen version has dropped the "and gets their money back" detail entirely — a real
rules omission, not just a phrasing difference — and reads more casually than the other copy.
Whoever reads the rules depends entirely on which screen they happened to land on first.

**Fix:** this content shouldn't be duplicated at all — same underlying explanation, two DOM
copies to keep in sync by hand. Worth factoring into one Jinja include/partial so there's a
single source of truth, not just patching the current text mismatch.

---

## 7. Host-time table settings are buried under "Advanced," but they're irreversible

**Where:** `index.html` L188-199 (`<details class="advanced">` in the host form)

`Reveal opponents' cards` and `Show game log` are collapsed behind an "Advanced" disclosure the
host has to think to open — yet the app's own hint text right there says *"These are fixed for
the whole table once the game starts — set them here, not adjustable mid-game."* Burying an
irreversible, table-wide decision behind a collapsed section increases the odds a host never
sees it before committing, then can't change their mind.

**Fix:** not necessarily un-collapsing everything — `host-think-time` is genuinely a minor
tuning knob and fine to hide — but `reveal-cards`/`show-logs` specifically read as consequential
enough to promote out of "Advanced" into the main form.

---

## 8. Minor / lower-priority notes

- **Bot-count touch targets on mobile**: the Easy/Medium/Hard number inputs and the bid-amount
  chips (1/2/3/4/6/8/10/12/15/20/25) are all comfortably tappable on desktop but run close to
  the ~44px minimum recommended touch target at a 375px viewport. Not broken, just tight —
  worth a look if mobile play becomes a bigger share of traffic.
- **"Watch as a spectator instead"** appears as a plain secondary button at the bottom of the
  join screen, same visual weight as everything else — easy to miss if someone's specifically
  looking to spectate rather than play. Not a blocker, just low-contrast for how common a path
  it might be (curious friends watching before deciding to join).
- **Profile popover positioning**: on mobile it opens flush against the right edge and covers
  part of the home tile behind it — expected popover-over-content behavior, not a bug, but
  worth a glance if the popover ever grows taller (e.g. more profile fields added later) since
  there's no scroll/overflow handling checked for that case.
