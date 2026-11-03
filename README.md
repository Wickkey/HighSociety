# HighSociety

An online version of the High Society auction card game for 3–5 players: a Python game engine,
bots from simple heuristics up to a self-play-trained neural network, and a browser client with
accounts, ratings and matchmaking.

**Play it now:** [highsociety.onrender.com](https://highsociety.onrender.com/). No install needed.

## What's in it

- **The game engine** (`highsociety/code/gamecore/`): rules, auctions (normal and disgrace),
  Faux Pas discards, elimination of the poorest player, scoring. Every game is reproducible from
  its seed and action log.
- **The website** (`web_server.py`, `highsociety/web/`). Flask with WebSockets:
  - **Hosting and joining:** host a public or private room, join by code, or **Play Now**
    (matchmaking, with bots filling empty seats).
  - **During a game:** spectating, rematches, and per-move time limits.
  - **Accounts and records:** guest accounts with an optional Google sign-in, **Elo ratings** and a
    leaderboard, player profiles, game history with replays, and achievements.
  - **Learning to play:** a How to Play page with a video tutorial, a guided practice game, and the
    written rules.
- **Bots** (`highsociety/code/ai/`), offered in the lobby as difficulties:

  | Difficulty | How it plays |
  |---|---|
  | Easy / Medium / Hard | Monte Carlo Tree Search (`mcts_bot.py`), with more search at each level |
  | **Insane** | a neural network trained by self-play reinforcement learning (`rl_bot.py`) |

  Every lobby bot is a named character with its own profile and Elo (`common/bot_roster.py`).
  The Insane bots are Batman, Thanos, Magnus Carlsen, Thalapathy and Rocky.
  For scripts and testing there are also plain heuristic bots: `pass`, `greedy` (raises with the
  cheapest card that beats the bid) and `capped` (the same, up to a budget per card).
- **CLI and socket play** (`scripts/`): hot-seat in one terminal, or networked over TCP with
  spectators.

## Requirements

Python 3.14, which is what production runs (pinned in `.python-version`, which Render also reads).

```
pip install -r requirements.txt          # app + bots (Flask, flask-sock, numpy, ...)
pip install -r tests/requirements.txt    # tests
```

## Playing

See [docs/PLAYING.md](docs/PLAYING.md) for the full instructions. Quick start:

```
python3 web_server.py                                        # the website: open http://localhost:8000
python3 -m scripts.play_cli                                  # local, one terminal, hot-seat
python3 -m scripts.network_server --players 2                # networked: host
python3 -m scripts.network_client --host <ip> --port 8888    # networked: each player
```

### Against bots from the command line

`scripts/play_cli.py` and `scripts/network_server.py` take `--bots`: a comma-separated list of bot
types from the `BOT_TYPES` registry in `highsociety/code/ai/__init__.py`. The types are `pass`,
`greedy`, `capped`, `easy`, `medium`, `hard` and `insane`. You're prompted only for the seats the
bots don't fill.

```
python3 -m scripts.play_cli --bots insane,medium                     # you + 2 bots
python3 -m scripts.network_server --players 3 --bots greedy,capped   # 1 human connects over the network
python3 -m highsociety.code.gamecore.dev_tools.simulate_bots --bots hard,insane,capped   # watch bots play
```

`--bot-think-time` (default 1 s) pauses before each bot move so a game can be followed; `0` runs
at full speed. Comparing bots over many games: `python3 -m scripts.bot_evaluator --help`.

**Writing your own bot:** see [docs/BOT_API.md](docs/BOT_API.md). Subclass `BasePlayer`,
implement `get_bid` / `choose_painting_to_discard` / `send_message`, and add it to `BOT_TYPES`.

### The Insane bot

`highsociety/code/ai/rl/` holds the game side of the neural-network bot:
- `encoding.py`: the board encoding, built from public game state
- `candidates.py`: the legal moves
- `network.py`: the network, re-implemented in **numpy** so the server doesn't need PyTorch
- `decision.py`: the move choice

The trained weights live in `rl/weights/`. `active.json` names the file the Insane bots play, and
each file's `.json` manifest records where it came from and how it was evaluated. Shipping better
weights means dropping in a new file and pointing `active.json` at it; no code change is needed.
Training itself happens outside this repository.

## Repository layout

```
web_server.py      the website (Flask + WebSockets); Render starts it with gunicorn web_server:app
highsociety/       game engine (code/gamecore), bots (code/ai), database layer (code/common/db),
                   web client (web/: templates, static JS/CSS)
scripts/           other entry points, run from the repo root as `python3 -m scripts.<name>`:
                   play_cli, network_server / network_client / network_spectator_client,
                   bot_evaluator, selfplay_job, verify_replay
docs/              PLAYING, BOT_API, SELFPLAY (bot-vs-bot data job), ARCHITECTURE_NOTES
tests/             pytest suite
render.yaml        Render Blueprint for the self-play cron job (the website is set up in Render's dashboard)
```

## Run tests

```
pytest
```

## Configuration

Game rules live in `highsociety/HSConfig.json`: starting cash, painting values, disgrace cards,
player limits and turn time limit. See `highsociety/code/HSConfig.md` for each field.

Environment variables are all optional. Copy `.env.example` to `.env` for local development; it's
loaded automatically. On a host, set them in its dashboard.

| Variable | What it turns on |
|---|---|
| `DATABASE_URL` | Postgres game history, accounts, ratings, achievements (see below). Unset: the app runs with no database at all. |
| `GOOGLE_CLIENT_ID` | "Sign in with Google" (ID tokens are verified server-side; no client secret needed). |
| `PUBLIC_BASE_URL` | The site's canonical address, used for canonical links and the sitemap. Unset: whatever host served the request. |
| `GA_MEASUREMENT_ID` | Google Analytics (GA4) on every page. Unset: no analytics script at all. |
| `BOT_POOL_SIZE` | Run MCTS bot decisions in N worker processes per difficulty instead of in-process. |
| `PORT` | Port for `python3 web_server.py` (default 8000). |

## Database (optional)

With `DATABASE_URL` pointing at a Postgres database (the live site uses [Supabase](https://supabase.com):
Project Settings → Database → Connection string), the app stores everything about
finished games.
- **Players:** guest or Google-linked accounts, Elo ratings and rating history, achievements.
- **Bots:** one identity per named bot.
- **Games:** results, rounds, and the complete ordered action log of every game.

The schema is created and migrated automatically at startup; every statement is idempotent, so
there's no manual migration step. See the docstring of `highsociety/code/common/db/game_history.py`.

Database writes happen after a game has finished, off the game's thread, so a slow or failing
database never affects play.

Every stored game is replayable from its seed and action log.
`python3 -m scripts.verify_replay --source game --sample 50` rebuilds stored games and checks that
every action and result reproduces (`highsociety/code/gamecore/recording/game_replay.py`). For
bulk bot-vs-bot games, stored in the same format in `selfplay_games`, see [docs/SELFPLAY.md](docs/SELFPLAY.md).

## Architecture: one engine, many kinds of players

`PlayGame` (`highsociety/code/gamecore/game_manager/gameplay.py`) never knows what kind of player
sits in a seat. It only calls `get_bid()`, `choose_painting_to_discard()` and `send_message()`, and
reads `.active`. A terminal player (`CLIPlayer`), a remote player (`NetworkPlayer`) and every bot
are implementations of that one interface (`gamecore/player/bot_interface.py`). PlayGame also gives
every player a live, read-only view of the game state (`AuctionHistory` plus the current auction).
The MCTS and Insane bots decide from that view alone, keeping nothing of their own across a game.

Remote play is layered so a new transport doesn't touch player logic or the engine:

- **`gamecore/network/transport.py`:** `Transport` ("send one JSON message" / "receive the next
  one"), with `SocketTransport` (raw TCP, `scripts/network_server.py`) and `WebSocketTransport`
  (browser WebSocket, `web_server.py`).
- **`gamecore/network/protocol.py`:** the JSON message shapes (`PLAYER_MOVE`, `PLAYER_INFO`,
  `AUCTION_UPDATE`, …), independent of how the bytes move.
- **`NetworkPlayer` / `NetworkSpectator`:** thin adapters from a `Transport` plus the protocol to
  the player interface, with no socket or threading code of their own.

`web_server.py` runs many games at once: a dictionary of `GameRoom`s, each with its own `PlayGame`
thread. It uses that same stack unchanged, adding only the lobby, accounts and persistence around it.

More design notes: [docs/ARCHITECTURE_NOTES.md](docs/ARCHITECTURE_NOTES.md).
