# Building a Bot

Two ways to build a bot, depending on where it runs:

- **Embedded** — subclass `BasePlayer` (`player.py`) and implement the three methods
  `highsociety/code/gamecore/player/bot_interface.py`'s `BotInterface` declares mandatory —
  `get_bid`, `choose_painting_to_discard`, `send_message` — then hand an instance to `PlayGame`
  directly. `BasePlayer` gives you bidding/card bookkeeping for free; `BotInterface` is an ABC, so
  forgetting one of the three raises `TypeError` immediately at construction, not partway through a
  game. `CLIPlayer`/`NetworkPlayer` are the two existing implementations — read either as a
  reference. Simplest option if your bot lives in this codebase.
- **Remote** — connect over the network like any other player (`network_client.py`'s protocol),
  in any language. This is what you want for an external bot-building competition, since entrants
  don't need this repo at all — just a TCP socket and JSON.

This doc covers both, with the remote/network path as the main focus since that's the one you
don't already have example code for elsewhere in the repo.

## The wire protocol, from a bot's point of view

Connect a TCP socket to the host/port `network_server.py` prints, then exchange newline-delimited
JSON objects — each message is one `json.dumps(...) + "\n"`.

### 1. Handshake

The server sends two prompts in sequence (username, then display name). Reply to each with:

```json
{"message_type": "IDENTIFY_ACK", "prompt": "your_answer_here"}
```

You'll then get an `IDENTIFY_SUCCESS` (or `IDENTIFY_ERROR` if something went wrong — check
`message_type` and bail out if so).

### 2. Messages you'll receive

Every message has a `message_type`. The ones a bot cares about:

| `message_type` | What it means | Needs a response? |
|---|---|---|
| `PLAYER_MOVE` | It's your turn. `constraints` tells you what's legal right now. | **Yes** |
| `AUCTION_RESULT` | An auction just finished — full structured history of it. | No |
| `PLAYER_INFO` | Informational text about your own state (cards, points, money). | No |
| `GLOBAL_EVENT` | General game narration (green card revealed, final standings, etc). | No |
| `GLOBAL_MOVE_INFO` | "It's X's turn" — informational, not addressed to you. | No |
| `INPUT_ERROR` | Your last response was invalid; try again. | No (but expect another `PLAYER_MOVE`) |

Every message also has `prompt` (a human-readable string — safe to ignore if you're parsing
structured fields instead) and `created_at` (unix timestamp).

### 3. `PLAYER_MOVE` has two different `move_type`s — check it before responding

A `PLAYER_MOVE` is asking one of two genuinely different questions, and you **must** check
`move_type` to know which one — don't try to infer it from `constraints` or the human-readable
`prompt` text, since `constraints.allowed_paintings` is populated on *every* `PLAYER_MOVE`
(it just reflects whatever paintings you currently own), not only on discard prompts.

```json
{
  "message_type": "PLAYER_MOVE",
  "move_type": "bid",
  "prompt": "Enter your bid for the auction: ",
  "requires_response": true,
  "constraints": {
    "min_bid": 0,
    "allowed_money_cards": [1, 2, 4, 8, 15],
    "allowed_paintings": [3, 7],
    "allowed_commands": ["pass", "fold", "quit"]
  }
}
```

- **`move_type: "bid"`** (the usual case) — respond with:
  ```json
  {"message_type": "RESPONSE", "prompt": "<your answer>"}
  ```
  where `<your answer>` is one of:
  - A number as a string, e.g. `"10"` — bid that single money card (must be in `allowed_money_cards`).
  - A list-looking string, e.g. `"[1, 2, 4]"` — bid multiple money cards at once (their values sum).
  - `"pass"` / `"fold"` — withdraw from the current auction.
  - `"quit"` — leave the game entirely.

- **`move_type: "discard_painting"`** — you're holding a FauxPas and just won a painting; you must
  discard one. Respond with the painting's **value** as a string, e.g. `"prompt": "7"` to discard
  the painting worth 7 — check `constraints.allowed_paintings` for which values you actually hold.
  `pass`/`fold`/`quit` are not valid answers here.

If you send an invalid answer for either kind, you'll get an `INPUT_ERROR` and another matching
`PLAYER_MOVE` — same `move_type` as before, so you can safely retry with the same logic path.

## Auction history — the `AUCTION_RESULT` message

This is the core data feed for building a bot that reasons about what's already happened, not
just its own turn in isolation. One `AUCTION_RESULT` message is broadcast to every connected
player and spectator immediately after each auction concludes — you don't need to poll for it.

```json
{
  "message_type": "AUCTION_RESULT",
  "prompt": "[auction_result] Painting → bob for 8",
  "requires_response": false,
  "created_at": 1780000000.123,
  "data": {
    "round_number": 3,
    "auction_type": "normal",
    "card": {
      "type": "Painting",
      "value": 7,
      "multiplier": 1,
      "is_green": false,
      "description": "Painting Card with value 7"
    },
    "events": [
      {"player": "alice", "action": "bid", "amount": 5, "cards": [5]},
      {"player": "bob", "action": "bid", "amount": 8, "cards": [3, 5]},
      {"player": "alice", "action": "pass", "amount": null, "cards": null}
    ],
    "recipient": "bob",
    "price_paid": 8,
    "cards_paid": [3, 5]
  }
}
```

Field reference (`data`):

- **`round_number`** — 1-indexed position of this auction in the game so far.
- **`auction_type`** — `"normal"` (highest bid wins) or `"disgrace"` (FauxPas/Passe/Scandale —
  first player to pass *takes* the card).
- **`card.type`** — the card's class name: `"Painting"`, `"PrestigeCard"`, `"FauxPas"`, `"Passe"`,
  or `"Scandale"`. Use this to tell auction types apart programmatically, not `auction_type` alone
  (a disgrace auction's `card.type` tells you *which* disgrace card it was).
- **`events`** — the full turn-by-turn sequence, in order. `action` is `"bid"` / `"pass"` /
  `"fold"` / `"quit"`; `amount` is that player's *total* bid after the action (only set for
  `"bid"`); `cards` is the list of individual money card values that make up `amount` (also only
  set for `"bid"` — e.g. a bid of 8 made with a 3-card and a 5-card is `"cards": [3, 5]`). Replay
  this list if you want to study bidding patterns, not just the final price.
- **`recipient`** — username who ended up with the card, or `null` if a normal auction had zero
  active bidders (nobody wanted it and nobody was forced to take it).
- **`price_paid`** — what `recipient` actually paid. For a disgrace auction this is always `0` —
  the recipient is whoever passed, and passing refunds their own bids; everyone else's bids in
  `events` were forfeited trying to avoid taking the card (see `README.md`'s architecture section
  on the disgrace-auction settlement strategy if you want the full rule).
- **`cards_paid`** — the individual money card values `recipient` handed over to make up
  `price_paid` (e.g. `[3, 5]` for a price of 8 paid with those two cards). Always `[]` for a
  disgrace auction, since `price_paid` is always `0` there too.

### Embedded/local equivalent

If your bot runs inside this codebase rather than over a socket, skip the network layer entirely
and call `game.get_auction_history()` on your `PlayGame` instance — it returns the exact same
list of `data`-shaped dicts, oldest first. Useful for fast local testing before pointing your bot
at a real server.

## A minimal example bot (Python, stdlib only)

Connects, always passes, and prints every `AUCTION_RESULT` it sees — a starting point, not a
strategy.

```python
import json
import socket

def send(sock, payload):
    sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))

def recv_line(buffer, sock):
    while "\n" not in buffer[0]:
        chunk = sock.recv(4096).decode("utf-8")
        if not chunk:
            return None  # server closed the connection (the game ended)
        buffer[0] += chunk
    line, buffer[0] = buffer[0].split("\n", 1)
    return json.loads(line)

sock = socket.create_connection(("localhost", 8888))
buffer = [""]

# Handshake
recv_line(buffer, sock)  # "Enter your username" prompt
send(sock, {"message_type": "IDENTIFY_ACK", "prompt": "my_bot"})
recv_line(buffer, sock)  # "Enter your display name" prompt
send(sock, {"message_type": "IDENTIFY_ACK", "prompt": "My Bot"})
recv_line(buffer, sock)  # welcome message

history = []
while True:
    msg = recv_line(buffer, sock)
    if msg is None:
        break  # game over, server closed the connection

    if msg["message_type"] == "AUCTION_RESULT":
        history.append(msg["data"])
        print(f"Auction #{msg['data']['round_number']}: "
              f"{msg['data']['card']['type']} -> {msg['data']['recipient']} "
              f"for {msg['data']['price_paid']}")

    elif msg["message_type"] == "PLAYER_MOVE":
        try:
            if msg.get("move_type") == "discard_painting":
                # "pass" is not valid here — must discard one of your own paintings.
                painting_value = msg["constraints"]["allowed_paintings"][0]
                send(sock, {"message_type": "RESPONSE", "prompt": str(painting_value)})
            else:
                send(sock, {"message_type": "RESPONSE", "prompt": "pass"})
        except (BrokenPipeError, ConnectionResetError, OSError):
            break  # connection dropped right as we tried to respond; nothing more to do
```

Note: this example (including the `move_type` check and the connection-closing handling above) was
actually run against a live server while writing this doc — an earlier version that always sent
`"pass"` without checking `move_type` hung forever the moment it was asked to discard a painting,
which is exactly why `move_type` exists.

## Testing your bot without a real opponent

`network_server.py --seed N` makes the whole game (deck order, turn order) reproducible, and
`python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --seed N` shows you the exact card
order a given seed deals before you even connect — useful for engineering a specific scenario to
test your bot's logic against. See `PLAYING.md` for the full record/replay workflow, which also
works for capturing a real bot-vs-bot match and replaying it deterministically later.
