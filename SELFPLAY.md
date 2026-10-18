# Self-play data for RL training

`selfplay_job.py` plays 5-player bot-vs-bot games back to back and stores each
one in Supabase until a target count (default **10,000**) is reached.
`verify_replay.py` proves the stored games are reproducible, using the
database as the only source of truth.

## What gets stored

Table **`selfplay_games`**: one row is one complete, replayable episode.

| column | meaning |
|---|---|
| `batch` | dataset name (`SELFPLAY_BATCH`, default `rl-v1`). Use a new name for a new dataset. |
| `seed` | the engine's per-game RNG seed |
| `seat_kinds` | bot type per seat, in turn order, e.g. `["hard","easy","medium","easy","hard"]` |
| `winner_seats` | winning seat indices (more than one on a tie, empty if nobody won) |
| `num_rounds`, `num_decisions`, `duration_ms` | quick stats (`num_decisions` counts actions) |
| `engine_version` | git commit the game was played on (`RENDER_GIT_COMMIT`) |
| `replay` | the full game record (JSONB). Format is documented in `highsociety/code/gamecore/recording/game_replay.py`. |

`replay` is the same record a website game is rebuilt from:
- the seed;
- the seats in turn order;
- `actions`, with exactly the rows and fields of the website's `game_actions` table: every bid
  (with its **money cards**), pass, quit and Faux Pas discard, by seat, with `think_ms`;
- per-round cards, winners and prices;
- final standings.

That is enough to rebuild every intermediate state for training.

These games are kept out of `games`, `player_games` and `ratings` on purpose.
Ten thousand bot-only games there would swing the bots' Elo, flood the
leaderboard and the global stats, and add nothing an episode row doesn't
already carry.

Every game is replayed from its own log before it's inserted. A game that
doesn't reproduce exactly is never stored.

Real website games are replayable the same way, straight from the regular history tables:
`games.seed`, `player_games.seat`, `game_rounds` and `game_actions`.

## Running on Render

1. Merge this branch, or point the job at it.
2. In Render: **New → Blueprint**, then pick this repo. Render reads `render.yaml`
   and creates only the `highsociety-selfplay` cron job. The web service is
   not touched.
3. Set `DATABASE_URL` on the job (the same Supabase URL the web service uses).
4. Trigger the first run with **Trigger Run** instead of waiting for the hour.

Each run plays for up to 11 hours (Render caps a cron run at 12). The hourly
schedule then resumes from the stored count. A run that's stopped (deploy,
cancel, SIGTERM) saves every finished game immediately. Once the target is
reached, each later run exits within seconds, so suspend the job at that point.

Everything can be set as an environment variable on the job:

| variable | default | |
|---|---|---|
| `SELFPLAY_TARGET_GAMES` | `10000` | total for the batch |
| `SELFPLAY_BATCH` | `rl-v1` | |
| `SELFPLAY_BOT_POOL` | `easy,medium,hard` | each seat drawn uniformly, per game |
| `SELFPLAY_PLAYERS` | `5` | |
| `SELFPLAY_WORKERS` | CPU count | **set this to the plan's CPU count**. A container can report the host's CPUs. |
| `SELFPLAY_MAX_RUNTIME_SECONDS` | `39600` | |

### How long it takes

Bot difficulty dominates. Measured single-core on an Apple-silicon laptop,
for one full 5-player game:

| table | time per game |
|---|---|
| all easy | ~0.9 s |
| all medium | ~10 s |
| all hard | ~67 s |
| uniform mix (default pool) | ~18 s |

Render CPUs are likely slower. Budget roughly 50–80 core-hours for 10,000
default-mix games: about a day on a 4-CPU plan, or about 2 days on 2 CPUs.
Watch the job's log: each flush prints the actual games/hour, and you can
rescale from there.

## Checking reproducibility

```bash
python3 verify_replay.py --source selfplay --batch rl-v1 --sample 500
python3 verify_replay.py --source game --sample 50       # website games, rebuilt from game_actions
```

This exits non-zero if any checked game diverges.

Disconnects replay exactly too. The engine records a disconnect, resign or
reconnect as an action, at a fixed checkpoint in the game, so a replay drops
the seat at the same moment. Only games recorded before this format (no
`games.seed`, or actions without a `seat`) are reported as "unsupported".

## Local run

```bash
python3 selfplay_job.py --target-games 20 --batch scratch --bot-pool easy --workers 4
```
