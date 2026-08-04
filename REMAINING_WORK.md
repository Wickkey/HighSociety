# What's Left in the Backend

A survey of what's genuinely incomplete, stubbed, or unbuilt, based on reading the current code —
not a wishlist. Every item below was verified against the actual source (grepped for real usage,
not assumed). Written 2026-08-02, after the CLI fix pass, network protocol rewrite, seed/record/
replay system, and Transport/protocol modularity refactor.

## Explicitly stubbed / defined-but-dead code

- ~~**Auction history isn't tracked.**~~ **Fixed and designed for bot consumption specifically**
  (per product decision — this is the primary interface an external bot-building competition would
  read). `auction_information.py` was redesigned as clean `BidEvent`/`AuctionRecord` dataclasses;
  `normal_card_auction`/`disgrace_card_auction` now populate `PlayGame.auction_rounds` as they run.
  Two ways to read it: `PlayGame.get_auction_history()` (local/embedded bots) or the `AUCTION_RESULT`
  message broadcast to every player/spectator right after each auction concludes (remote bots —
  see `BOT_API.md`). Along the way, also added an explicit `move_type` field (`"bid"` vs
  `"discard_painting"`) to `PLAYER_MOVE` messages, since a bot previously had no way to tell a bid
  prompt from a FauxPas discard prompt without parsing human-readable text (confirmed by writing
  BOT_API.md's example bot and watching it hang on a discard prompt before the fix). Verified live
  against a real two-bot game over real sockets, not just unit tests.
- **`RefundAllSettlement` isn't reachable from outside a test.** The pluggable disgrace-auction
  settlement strategy (`disgrace_settlement.py`) has two implementations, but `PlayGame` defaults
  to `ForfeitSettlement` and neither `main.py` nor `network_server.py` expose a flag to choose the
  other. If you want the "official rules" refund-everyone behavior available to a real user, it
  needs a `--settlement` flag or config entry.
- ~~**Spectator `CHAT` messages don't go anywhere.**~~ **Fixed.** Spectators now get a receiver
  thread and a per-connection chat-relay thread (`network_server.py::_spectator_chat_listener`).
  `network_spectator_client.py` sends typed input as chat: plain text → `target="all"` (players +
  other spectators), `/spectators <message>` → `target="spectators"` only. Never echoed back to the
  sender; a mismatched `game_id` on an incoming chat message is dropped (reusing the validation
  pattern from the earlier `game_id` fix). Players can now receive `CHAT` too (`protocol.py`'s
  `PLAYER_MESSAGE_TYPES` gained it). Tests:
  `test_end_to_end_socket.py::test_spectator_chat_to_all_reaches_players_and_other_spectators_not_sender`,
  `::test_spectator_chat_to_spectators_only_does_not_reach_players`,
  `::test_spectator_chat_with_mismatched_game_id_is_dropped`.

## Config values that are silently ignored

- **A recording doesn't pin the config it was made under.** `SessionRecorder` saves the seed, but
  not a snapshot/hash of `HSConfig.json` at record time. If painting values, disgrace card counts,
  etc. change before you replay, the replay could silently diverge from the original game (or hit
  a `ReplayMismatch`) with no clear "your config changed" error message.

## Networking robustness gaps

- **No reconnection support.** Once a `NetworkPlayer`'s transport disconnects (`active` flips to
  `False`), there's no path back in — same effect as quitting. If a player's WiFi blips mid-game,
  they're permanently out, not just paused.
- **One game per server process.** `start_server()` runs exactly one `PlayGame` and exits. There's
  no lobby/matchmaking layer for hosting multiple concurrent games from one running server.

## Bigger unbuilt capabilities

- **No bot/AI player.** Every "bot" that's existed so far (in this conversation's manual testing,
  and conceptually as future training-data consumer) has been ad hoc test code, never a real
  `BotPlayer` class implementing `BasePlayer`. If you want a fill-empty-seats or practice-against-
  the-computer mode, that's unwritten.
- **No mid-crash resume.** Record/replay reproduces a game from scratch, decision by decision — it
  doesn't snapshot in-progress state, so a server crash mid-game loses that game permanently (the
  recording file, if `--record` was on, only has *decisions made so far*, not a resumable state).
- **No web client / `WebSocketTransport`.** Per the earlier architecture discussion, this is the
  one new component a browser frontend needs — deliberately not built, since the whole point of
  the `Transport` abstraction was to defer this until it's actually wanted.
- **No authentication.** Anyone who can reach the port can connect and claim any username; no
  reconnection tokens, no spoofing protection. Fine for a trusted LAN/friends game, not fine for
  anything more exposed.

## Missing test coverage

- No tests for the connection-acceptance edge cases above (partial handshake failure, heartbeat
  timeout actually kicking a stale player, concurrent connection races).
- No load/stress testing (many concurrent spectators, rapid reconnect attempts).
- `AuctionInformation`/`auction_rounds` has no tests, consistent with being unused.

## Minor polish (not correctness bugs)

- Per-turn time display is a single "Time left: Xs" message, not a live ticking countdown.
- ~~No colorized/formatted CLI output.~~ **Fixed.** `common/utils/terminal_colors.py` provides
  ANSI helpers that auto-disable when not attached to a real terminal (respects `NO_COLOR`/
  `FORCE_COLOR`), wired into `CLIPlayer` (by `message_type`), `CLIHost`/`network_client.py`/
  `network_spectator_client.py` (broadcast text, by the emoji markers `gameplay.py` already uses).
  Verified escape codes appear under `FORCE_COLOR=1` and are completely absent otherwise (piped/
  test output unaffected — confirmed 0 occurrences without it).
- `highsociety/HSConfig.json`'s `abs_root_dir` is still a hardcoded absolute path (used for the log
  directory) — `get_all_configurations()` itself was made portable, but this one field wasn't.

## Explicitly *not* gaps (already-settled design decisions — don't re-litigate these)

- Disgrace auctions: non-passers forfeit their money by default (`ForfeitSettlement`) — decided,
  see the settlement-strategy section above for how to switch it, not whether to.
- Points/money exact ties: declared as an explicit tie, no further tiebreaker — decided.
- The green-card limit ending a game before the deck is exhausted — intentional, documented in
  `HSConfig.json`'s own comments.
