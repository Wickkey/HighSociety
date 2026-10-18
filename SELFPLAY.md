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
| `num_rounds`, `num_decisions`, `duration_ms` | quick stats |
| `engine_version` | git commit the game was played on (`RENDER_GIT_COMMIT`) |
| `replay` | the full log (JSONB). Format is documented in `highsociety/code/gamecore/recording/game_replay.py`. |

`replay` holds:
- the realized deck order;
- the seat order and starting seat;
- every decision in order, keyed by seat. A bid is the actual **money cards** played, not just the total, and Faux Pas discards are included;
- per-round winners and prices;
- final standings.

That is enough to rebuild every intermediate state for training.

These games are kept out of `games`, `player_games` and `ratings` on purpose.
Ten thousand bot-only games there would swing the bots' Elo, flood the
leaderboard and the global stats, and add nothing an episode row doesn't
already carry.

Every game is replayed from its own log before it's inserted. A game that
doesn't reproduce exactly is never stored.

Real website games now also get a replay log, in **`game_replays`**
(`game_id` → `replay`), written in the same transaction as the `games` row.

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
python3 verify_replay.py --source game --ids 812          # a real website game
```

This exits non-zero if any checked game diverges.

Known limitation: a human who **disconnects** mid-game becomes inactive at an
arbitrary moment, which no decision log can place exactly. Those games are
stored faithfully but reported as "unsupported" rather than replayed. Bot
games, and humans who finish or quit explicitly, replay exactly.

## Local run

```bash
python3 selfplay_job.py --target-games 20 --batch scratch --bot-pool easy --workers 4
```
