# What's Left in the Backend

A survey of what's genuinely incomplete, stubbed, or unbuilt, based on reading the current code —
not a wishlist. Every item below was verified against the actual source (grepped for real usage,
not assumed). Written 2026-08-02, after the CLI fix pass, network protocol rewrite, seed/record/
replay system, and Transport/protocol modularity refactor.

## Explicitly stubbed / defined-but-dead code

- **Auction history isn't tracked.** `AuctionInformation` (`auction_information.py`) and
  `PlayGame.auction_rounds` exist, and `add_bid()` is fully implemented — but nothing ever calls
  it. `player_info`'s `"auction_history": []` is literally commented `# to implement` in the
  source. If you want a post-game or in-game log of "who bid what on which card," this needs
  wiring into `normal_card_auction`/`disgrace_card_auction`.
- **`RefundAllSettlement` isn't reachable from outside a test.** The pluggable disgrace-auction
  settlement strategy (`disgrace_settlement.py`) has two implementations, but `PlayGame` defaults
  to `ForfeitSettlement` and neither `main.py` nor `network_server.py` expose a flag to choose the
  other. If you want the "official rules" refund-everyone behavior available to a real user, it
  needs a `--settlement` flag or config entry.
- **Spectator `CHAT` messages don't go anywhere.** `network/protocol.py` defines a `CHAT` message
  shape, but no spectator client ever sends one, and spectators don't even get a receiver thread
  on the server (`accept_spectators` never calls anything like `start_receiver_thread` for them).
  It's a payload shape with no sender and no listener.
- ~~**`LogType.SECURITY` is never invoked.**~~ **Fixed as a side effect of the `game_id` validation
  fix below** — `NetworkPlayer._belongs_to_this_game()` now logs a mismatched `game_id` there.

## Config values that are silently ignored

- **`max_players` / `min_players` in `HSConfig.json` are dead.** Neither `main.py`'s
  `get_num_players()` nor `network_server.py`'s `--players` argument reference them at all — both
  only hardcode a `< 2` check. You can start a 20-player game today even though the config claims
  a max of 5.
- **A recording doesn't pin the config it was made under.** `SessionRecorder` saves the seed, but
  not a snapshot/hash of `HSConfig.json` at record time. If painting values, disgrace card counts,
  etc. change before you replay, the replay could silently diverge from the original game (or hit
  a `ReplayMismatch`) with no clear "your config changed" error message.

## Networking robustness gaps

- ~~**A failed handshake silently reduces the player count.**~~ **Fixed.** `accept_players()` now
  waits for `expected_players` *successful* handshakes, not just that many accepted connections —
  a client that fails IDENTIFY no longer permanently steals a slot; the server keeps accepting
  replacement connections until enough real players actually join. Regression test:
  `unittest/network/test_end_to_end_socket.py::test_a_failed_handshake_does_not_permanently_steal_a_player_slot`
  (verified it fails against the old logic, passes against the fix).
- **No reconnection support.** Once a `NetworkPlayer`'s transport disconnects (`active` flips to
  `False`), there's no path back in — same effect as quitting. If a player's WiFi blips mid-game,
  they're permanently out, not just paused.
- ~~**No `game_id` validation.**~~ **Fixed.** `NetworkPlayer.get_bid()`/`choose_painting_to_discard()`
  now silently discard (and log to the security logger — the first real use of `LogType.SECURITY`)
  any incoming message whose `game_id` is present but doesn't match the player's own; the server
  handshake (`accept_players`/`accept_spectators`) rejects an `IDENTIFY_ACK` the same way. A
  *missing* `game_id` stays permissive, for lightweight clients/tests that don't set one. Tests:
  `test_network_player_protocol.py::test_get_bid_ignores_a_message_with_mismatched_game_id` (+
  the `choose_painting_to_discard` equivalent) and
  `test_end_to_end_socket.py::test_handshake_rejects_a_mismatched_game_id_and_waits_for_a_replacement`.
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
- No colorized/formatted CLI output (plain `print()` throughout).
- `highsociety/HSConfig.json`'s `abs_root_dir` is still a hardcoded absolute path (used for the log
  directory) — `get_all_configurations()` itself was made portable, but this one field wasn't.

## Explicitly *not* gaps (already-settled design decisions — don't re-litigate these)

- Disgrace auctions: non-passers forfeit their money by default (`ForfeitSettlement`) — decided,
  see the settlement-strategy section above for how to switch it, not whether to.
- Points/money exact ties: declared as an explicit tie, no further tiebreaker — decided.
- The green-card limit ending a game before the deck is exhausted — intentional, documented in
  `HSConfig.json`'s own comments.
