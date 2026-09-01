# CLAUDE.md

Project-specific instructions for Claude Code when working on this repo.

## Visual/CSS fixes: check the whole screen, not just the reported element

When asked to fix a visual issue (alignment, spacing, sizing) on one
specific element, treat that as a prompt to review the *whole screen or
panel* it lives on for the same class of problem — not just the one
element named. A narrow fix can leave sibling elements looking
inconsistent with it, and with the rest of the app's screens, even though
nobody has flagged them yet.

Concrete example from this project: asked to fix the matchmaking screen's
back-button alignment (it was centered instead of top-left like every
other screen's back link), the root cause was `#screen-matchmaking .panel`
setting `text-align: center` on the whole panel. Fixing only the back
button left the "Players" label pinned left by its own separate override
while its input stayed centered by a third rule — a mismatch nobody had
pointed at yet, visible in the very same screenshot taken to verify the
first fix, that got reported separately after the first "fix" shipped.

Before calling a visual fix complete:
1. Take a full screenshot of the affected screen (not just the element).
2. Actually look at it — don't just confirm the one reported symptom is
   gone.
3. Compare it against sibling screens/components using the same visual
   pattern (other back links, other form fields, other panels) for
   consistency, since a shared CSS rule affecting one often affects others
   the same way.

## Loading state: reserve the layout, never hide-then-reveal a tile

A tile/section whose content depends on an async fetch must keep the
same footprint whether it's loading or has real data. Show a loading
spinner (`.tile-loading-spinner`, see `lobby.css`) or skeleton *inside*
the tile's already-reserved slot; never toggle the tile's own
`.hidden`/visibility based on whether its data has arrived yet. If
there's genuinely no data once the fetch resolves (a new player with no
games, a guest with no rated history), replace the loading state with a
short explanatory message *in that same slot* — still never collapsing
or hiding the tile itself.

Two related real bugs this fixes:
- **Layout shift / relocation**: a tile that stays hidden until its
  fetch resolves, sitting above another tile that resolves faster,
  visibly shoves that second tile down (or "relocates" it) once the
  slower one finally pops in. Reserving the slot up front — synchronously
  if the caller already knows enough to decide (e.g. a local
  `profile.google_id` check), or via a fixed-size loading state
  otherwise — eliminates this regardless of which fetch finishes first.
- **"Loading one tile at a time"**: several independently-loading tiles
  on one screen, each hidden until its own data arrives, reads as the
  whole screen loading sequentially even when every fetch actually
  started in parallel — because visually nothing appears until each one
  individually finishes. Reserving every tile's slot immediately (all in
  their own loading state) makes the parallelism visible instead of
  looking accidental.

Concrete example from this project: the Account/Player Profile screens'
Elo chart used to live in its own tile, hidden until an async fetch (plus
a real charting library load, the first time it's needed) resolved —
whenever that took longer than the Game History tile below it, Game
History would settle in first and then visibly get pushed down once the
chart popped in late. Fixed by making the chart tile (and Game History)
permanently part of the layout, each swapping only their own internal
content between a spinner, real data, or an empty-state message — see
`ui/eloChart.js`'s own module comment for the full writeup.
