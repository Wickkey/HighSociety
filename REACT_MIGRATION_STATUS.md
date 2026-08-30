# React frontend migration — status

Working notes for resuming this branch (`feature/react-frontend`) later,
in a fresh session with none of the current conversation's context. Written
mid-migration, right after Phase 3's checkpoint, at the user's request to
pause and hand off cleanly.

The approved plan lives at `.claude/plans/foamy-cooking-lark.md` — read that
first for the full phase breakdown and the architecture decisions made
during plan mode (TypeScript, Context+useReducer, CSS Modules, React
Router, checkpoint-after-each-phase). This file is the "what's actually
true right now" companion to that plan, not a replacement for it.

## Where things stand

Three phases done, each its own commit on this branch, each independently
verified live against the real backend before moving on:

- `0b84aab` — **Phase 1**: infra + Login + Home.
- `8b95243` — Node version pin (`.node-version`) for the Render build.
- `fd5dd9c` — **Phase 2**: lobby, hosting, joining, matchmaking.
- `498926e` — **Phase 3 (core)**: live gameplay — the auction/bidding loop.
- `45d7a7f` — a real bug fix found while verifying Phase 3 (see below).

**Not started:** Phase 4 (Finished screen + Elo reveal, Account,
Leaderboard, My Games, Achievements).

**Deliberately deferred within Phase 3** (state is correct; these are pure
decoration on top of it, not yet built):
- The transient event-toast queue (brief "X raised to Y" announcements
  over the auction card).
- Quick-reaction emoji bubbles.
- The countdown-to-start and final-green-card overlays.
- The move timer's urgent double-beep.

None of these losing anything functionally — the same narration already
shows up as plain text in the persistent game log (`GameLog.tsx`), which
*is* built and working.

## Architecture, if you're picking this up fresh

```
frontend/src/
  api/            typed fetch wrappers (client.ts), stale-while-revalidate
                  games cache (gamesCache.ts, built in Phase 1, not yet
                  consumed by a My Games screen -- that's Phase 4)
  ws/             socket.ts (generic connect/send/dispose, identity-guarded
                  close), protocol.ts (typed IDENTIFY/rematch messages)
  hooks/
    usePolling.ts               generic "fire now, then every Nms" loop
    usePlayerConnection.ts      player WS lifecycle + IDENTIFY handshake
    useSpectatorConnection.ts   spectator WS lifecycle (read-only)
    usePlayerGameSession.ts     bundles a player connection + the game
                                 reducer + action senders (bid/pass/
                                 discard/resign/chat) + delivery watchdog
    useSpectatorGameSession.ts  spectator counterpart (no actions/watchdog)
    useMoveTimer.ts             local per-move countdown display
    useMatchmaking.ts           Elo matchmaking ticket lifecycle
    useLeaveRoomGuard.ts        the "leave mid-game?" confirm, used by
                                 AppShell's sidebar/title/profile nav
  state/
    ProfileContext.tsx     persistent device identity (Phase 1)
    RoomContext.tsx        current room code + one polled /api/status
                            (Phase 2) -- also owns `connectionRole`
                            ('none'|'player'|'spectator'), which is how
                            useLeaveRoomGuard knows whether to prompt
    ConfirmDialogContext.tsx  the one app-wide promise-based confirm()
    gameReducer.ts          the live game's state machine (Phase 3) --
                            pure, unit-tested, replaces the old app's
                            game/gameState.js + game/gameEvents.js
    gameSelectors.ts        pure display helpers (computePoints,
                            describeCard, orderedOpponentUsernames, ...)
    rejoin.ts               rejoin-token localStorage helpers
  screens/
    Login.tsx, Home.tsx     Phase 1
    Matchmaking.tsx         Phase 2 (the /play route)
    Room/                   Phase 2: Room.tsx (route entry) -> Lobby.tsx
                            -> PlayerPanel.tsx / SpectatorPanel.tsx /
                            WaitingRoom.tsx / LiveGamePlaceholder.tsx
    Game/                   Phase 3: GameScreen.tsx / SpectateScreen.tsx
                            compose AuctionPanel/OpponentsList/MyPanel/
                            MovePanel/GameLog/ChatPanel around one session
    ComingSoon.tsx          placeholder for every Phase 4 screen
  components/
    AppShell.tsx            sidebar + topbar, wired to useLeaveRoomGuard
    RecentGamesWidget.tsx   Phase 1, on the Home screen
```

**Key design decision worth knowing before touching the game layer:**
in-game WebSocket messages flow into `gameReducer` via a plain callback
(`onGameMessage`, called synchronously from the socket's `onmessage`
handler) — NOT through `usePlayerConnection`/`useSpectatorConnection`'s own
React `state`. This was a real risk caught during design, not
after-the-fact: React 18 batches state updates scheduled from outside its
own event system (a WebSocket handler included), so `setState(latestMsg)`
per message could silently coalesce two auction events into one render and
drop the earlier one. A reducer's `dispatch` doesn't have that problem
(React guarantees the reducer runs once per dispatched action, in order).
If you're extending this, keep new server-message handling going through
`dispatch`, not through any hook's own `useState`.

## Real bugs found + fixed while verifying (not hypothetical — all live-reproduced)

1. **React 18 StrictMode double-invoke broke reconnect/auto-join.** A
   `useRef`-guarded "run this exactly once" effect (the reconnect attempt
   in `LiveGamePlaceholder`, and matchmaking's auto-join-on-match in
   `PlayerPanel`) silently broke in dev: StrictMode's mount→cleanup→mount
   dance killed the one connection the ref-guard allowed, then refused to
   make the necessary second one. Fixed by making both effects
   unconditional — `open()` always tears down any previous connection
   before reconnecting, so re-running it is a safe no-op-if-redundant,
   which is exactly what StrictMode expects. Production is unaffected
   (StrictMode's double-invoke is dev-only).
2. **A connected socket receiving real game messages looked
   "disconnected."** Once a message with `message_type` outside
   IDENTIFY/IDENTIFY_ERROR/IDENTIFY_SUCCESS arrives, the connection phase
   becomes `'game'`. Neither `PlayerPanel` nor `LiveGamePlaceholder` had a
   render case for that phase initially, so they fell through to their
   join-again/spectate-fallback UI despite being fully connected and
   mid-game. Fixed with a shared `ConnectedStub` first, then properly
   replaced by the real `GameScreen`/`SpectateScreen` once those existed.
3. **`/room/<code>` 404'd on a hard reload or shared link.** Flask's
   `index()` route list (in `web_server.py`) covered every sidebar screen
   but never included `/room/<code>` — the one path React Router treats as
   a real client-side route rather than a query param. In-app navigation
   never noticed (React Router intercepts it client-side); a reload or a
   shared room URL hit Flask directly and 404'd. Fixed by adding
   `@app.route("/room/<code>")` to that same view function.

## Verification approach used throughout

Live Playwright scripts against the real Flask backend (never mocked),
run twice per phase where it mattered: once through `vite dev`'s proxy,
once against Flask serving the actual `npm run build` output directly (the
more production-representative check — the dev-proxy path has its own
irrelevant quirks, e.g. Vite's `base: '/static/dist/'` config makes a hard
reload of a deep route 404 *in dev only*, unrelated to any real app bug).

To repeat this locally:
```bash
# terminal 1 -- backend, no DB needed for most flows
DATABASE_URL="" python3 web_server.py --port 8000

# terminal 2 -- either:
cd frontend && npm run dev            # dev server + proxy, http://localhost:5173/static/dist/
# or, more production-like:
cd frontend && npm run build          # then hit http://localhost:8000/ directly
```
Scratch Playwright scripts from this session aren't checked in (they lived
in the session's scratchpad dir) — quick to rewrite if needed; the pattern
throughout was: log in as a fresh guest per browser context, drive the UI
via `page.click`/`getByLabel`/`getByPlaceholder`, assert on `page.textContent('body')`
substrings, screenshot key states, and collect `console.error`/`pageerror`
events (filtering out the one known-benign `Invalid frame header` message
on deliberate socket teardown — see below).

## One known, investigated, benign console message

`WebSocket connection to '...' failed: Invalid frame header` appears in
devtools whenever this app closes a socket itself (leaving a room,
superseding a connection with a fresh one, reconnecting). Confirmed via
`git show main:.../network/websocket.js` that the *old*, unmodified
vanilla-JS frontend uses the exact same unconditional `ws.close()`
pattern — this predates the migration, isn't a regression, and doesn't
affect functionality (every check that exercises the paths that trigger it
still passes). Left alone rather than speculatively "fixed."

## Open question from the user, not yet resolved

User reported the deployed app (on Render, this branch) felt "sluggish"
and hasn't yet said which specific thing was slow. Prime suspect raised
but unconfirmed: Render's free/starter tier cold-starts after ~15 min
idle (20-50s to wake the dyno), which would explain a single slow load
with no code-level cause. Other possibilities flagged but not
investigated: polling-driven UI (room list/seat counts update on a
1.5-2s poll cycle by design, not instantly) vs. an actual responsiveness
bug in a specific interaction. **Next session: ask what specifically felt
slow before assuming which of these it is.**

## UI polish — deliberately not done yet

Per explicit user instruction: get the functionality/logic right first
(modularity, correct state machine, live-verified against the real
backend), UI/visual feedback comes in a dedicated pass **after** Phase 4
makes the app feature-complete — polishing one screen's spacing/colors now
risks redoing it once every screen exists and needs to look consistent
together (see CLAUDE.md's "check the whole screen, not just the reported
element" principle, scaled up to "check the whole app"). The current
`Game.module.css`/`Room.module.css` styling is functional, not final.

## Suggested next steps, in order

1. Get the user's answer on what "sluggish" actually meant; fix if it's
   real, otherwise note it's Render's tier and move on.
2. Either finish Phase 3's deferred polish (toasts, reactions, overlays,
   beep) or go straight to Phase 4 (Finished/Elo-reveal, Account,
   Leaderboard, My Games, Achievements) — user's call, ask if unstated.
3. Phase 4 will need to reuse `api/gamesCache.ts` (built in Phase 1 for
   the Recent Games widget, not yet consumed elsewhere) for My Games'
   pagination, per that file's own comments.
4. The user previously asked (much earlier, pre-migration) for the
   ranking/Elo chart to be "removed from the leaderboard page, don't
   delete the code, I'll ask you to include it somewhere else" — still
   unresolved even in the old app; the new React Leaderboard screen has no
   chart component built at all yet. Ask where they want it before
   building the Leaderboard screen.
5. Final phase per the plan: a full side-by-side regression pass against
   `main`'s live app before proposing this branch for merge.

## Standing reminders (from project/session conventions, still apply)

- Log plan to GitHub issue #9 before each work item, comment with the
  commit SHA after — done for Phases 1-3 above; keep doing it.
- Commit and push often; this session did so after every phase and after
  the standalone bug fix, never batching multiple unrelated changes into
  one commit.
- Full Python test suite (520 tests) has stayed green through every
  change so far, including the one backend route fix — keep running it
  after any `web_server.py` touch, not just frontend changes.
