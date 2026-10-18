# Why the pre-game-start backend isn't fully game-agnostic yet

Written 2026-08-18, in response to a request to make "everything before
the game starts" work for any game, not just High Society. This documents
what's actually generic today, what isn't, exactly where the coupling
lives, and why closing that gap wasn't attempted as part of the overnight
matchmaking/UI work — so the decision is on record and revisitable.

## What's genuinely game-agnostic today

- **`highsociety/code/common/matchmaking.py`** — built from scratch this
  session with zero imports of anything HighSociety-specific. Every
  function that needs to actually *do* something with a match takes a
  `create_room_fn(usernames) -> room_code` callback instead of importing
  `GameRoom`/`PlayGame` directly. This was easy to get right because it's
  new code with no existing behavior to preserve.
- **Auth / identity** (`game_history.py`'s `players` table, the
  `/api/auth/*` routes) — usernames, Google linking, Elo. None of it
  knows or cares what game is eventually played.

## What isn't, and exactly why

The room/lobby layer that already existed (`GameRoom` in `web_server.py`)
is where the coupling lives. Three concrete places:

1. **`GameRoom.run_game()`** (`web_server.py`) directly constructs
   `PlayGame(players=..., spectators=..., mode='network', game_id=...,
   seed=..., turn_duration=..., auction_history=...)`. `AuctionHistory`
   isn't a generic "game state" concept — it's the bidding-history data
   structure specific to an auction-card game. A different game (say, a
   trick-taking game) wouldn't have an `AuctionHistory` at all; it would
   have some other shape of mid-game state.

2. **Rematch voting** (`_default_rematch_bot_mix`,
   `_maybe_start_rematch`, `_start_rematch_request`) reasons about seats,
   bot difficulty presets, and "who's still eligible" in ways that assume
   this exact game's seat/bot model.

3. **Game-history recording** (`_record_game_history`) writes `points`,
   `money_left`, `eliminated` into the database — High Society's actual
   scoring vocabulary, not a generic result schema.

None of these are "just starting the game." They're bookkeeping that
happens *around* the game, but they're written in terms of this game's
specific rules.

## Why this wasn't refactored tonight

To make that layer swappable, the shape needed is roughly a `GameEngine`
interface — something like `create(players, config) -> Game`,
`Game.play()`, `Game.get_results() -> <generic schema>` — that
HighSociety would implement, and a second game could implement
differently.

The problem: designing that interface right now means designing it
against exactly **one** concrete implementation. That's a well-known trap
in abstraction design — with only one real example, it's very easy to
guess wrong about which parts are truly generic versus which parts just
*happen* to look generic because there's nothing yet to contrast them
against. The two common failure modes are:

- **A leaky abstraction** that still quietly assumes High-Society-shaped
  data somewhere (e.g. a "result" schema that turns out to still need
  `money_left` because nothing else was ever tried against it).
- **A needlessly flexible one** — config options and hooks added "in
  case a future game needs them," never exercised by the one real game
  that uses the interface, and never validated as actually being the
  right hooks.

The reliable way to find the right abstraction is usually to build a
*second* real case first and extract what's actually shared afterward —
not to design it speculatively upfront from a single example.

On top of that: `GameRoom`, the rematch flow, and game-history recording
are the most heavily tested, most "live in production" code in this app —
real games get played through this path every day. A wrong guess at the
abstraction risks a subtle regression to gameplay that already works
correctly, and this was done overnight with no way to check in and catch
a bad call early. The new matchmaking code carried no such risk if
something in it turned out wrong — nothing depended on it yet.

## What would actually unblock this

The honest prerequisite is: **what would a second game concretely look
like?** Even a rough sketch — turn-based vs. simultaneous, fixed vs.
variable seat count, what a "result" needs to contain, whether spectators
still make sense — would surface which of the three coupling points above
are real architectural boundaries versus incidental. Worth doing
deliberately, with the actual shape of a second game in view, rather than
guessed at from High Society alone.
