# .RL: how the Insane bot was trained

The research workspace behind the lobby's **Insane** bots: a neural network trained to play High
Society by self-play reinforcement learning. Everything is here except the heavy artifacts
(network weights `*.pt` / `*.npz` and the cached bot-game data `*.pkl`), which `.gitignore` keeps
out of the repository. The weights the website plays with live in `highsociety/code/ai/rl/weights/`.

Python environment: the `ptorch` conda env (PyTorch, numpy). Never touch the real database while
experimenting: everything here sets `DATABASE_URL=""`.

## Where it ended up

`insane-v2` (run v2a, iteration 1,900) is what the website plays. Chance is 26% at mixed 3–5 player
tables.

| Opponents | Result |
|---|---|
| MCTS Medium | 47% wins, 8% eliminated: v2a iteration 1,000, 150 fresh games |
| MCTS Medium | ~50% wins in the 30-game progress checks around iteration 1,900 |
| Capped/Greedy bots | 50–62% wins in the progress checks |

The previous live bot (sp6, ~iteration 100) won 13% against MCTS Medium. A move takes about 0.5 ms
on the server.

## The pieces

**The game ↔ network interface (shared by everything)**
- `state_encoding.py`: one decision → a (27, 6) board.
  - Rows: 11 money cards, 10 paintings, 3 Prestige, 3 disgrace cards.
  - Columns: you, up to 4 opponents in turn order, and the game (deck / on auction / who is still
    in / high bidder).
- `rl_seat.py`:
  - `RLSeat` plays a real engine game.
  - The 1,024 possible moves (pass + every set of ≤ 5 money cards, `SUBSETS`) and `legal_bids`.
  - `play_game` / `play_selfplay`.
  - `auction_ledger`: replays a finished game auction by auction, for the rewards.
- `rl_reward.py`: the reward.
  - **Final place:** 1st +2, 2nd 0, the rest down to −1.
  - **After every auction:** points gained; a two-sided spend term (money used up to the poorest
    player's level is rewarded, a growing lead as top spender is penalised); money made others pay;
    a charge for each auction ended as the top spender.
  - **At game end:** a charge for wasted money.
  - The comments record why each term exists.

**v1: score the board after every legal move** (runs sp1–sp9)
- `rl_model.py` (`ScoreNet`): attention over the 27 rows → one score per afterstate.
- `rl_train.py` + `run_training.sh`: the REINFORCE trainer.
  - Self-play with a pool of past copies, a league of fixed opponents, and occasional engine bots.
  - Progress checks every 25 iterations; auto-stop if it collapses.
  - Checkpoints, crash restart, and a periodic restart that works around PyTorch's Mac-GPU memory leak.
- `dashboard.py`: the self-refreshing HTML dashboard each run writes (`runs/*/dashboard.html`).
- `export_weights.py`, `check_rl_bot.py`: v1 export and server-match check. The server no longer
  runs v1.
- `RL_exploration.ipynb` (sections 1–7), `00_explore_selfplay.ipynb`: the exploration notebooks:
  data, encoding, imitation warm start, and the reward rationale.

**v2: read the board once, one output per move** (run v2a, what ships)
- `v2/model_v2.py`: `PolicyNet`, the same attention body with 1,024 outputs and illegal moves
  masked. It has an optional value head, but the critic is switched off.
- `v2/gen_distill.py`, `v2/distill_v2.py`: distil sp9 (iterations 1,000 / 1,300) into v2 from 2,000
  of its games, using its full probability spread over moves. v2 matched sp9's move 90% of the time.
- `v2/train_v2.py` + `v2/run_training_v2.sh`: the v2 trainer.
  - Table themes: mixed, all sp9, all own copies, all conservative, all aggressive, all MCTS Medium.
  - Variable-appetite Capped bots (`v2/bots_v2.py`).
  - MCTS Medium checks.
- `v2/analyze.py`, `v2/bench_medium.py`, `v2/odd_moves.py`: measuring on fresh deals.
  - `analyze.py`: places, overpaying, eliminations, end-game behaviour.
  - `bench_medium.py`: games against MCTS bots.
  - `odd_moves.py`: decisions a human would question.
- `v2/export_v2.py`, `v2/check_v2_server.py`: export a checkpoint for the website, and prove the
  server's numpy network makes the same moves as PyTorch.

## The runs (each folder has `train.log`, `metrics.jsonl`, `dashboard.html`, `supervisor_notes.md`)

| Run | What it tried | What happened |
|---|---|---|
| sp1 | Imitation warm start → self-play, middle places slightly positive | Learned to never bid: hoarding money means never being the poorest |
| sp2 | Places +1 … −1 evenly | Swung from overspending to hoarding within ~50 iterations |
| sp3 | +1 extra for winning | Hoarded again: at an all-hoarding table, whoever spends gets eliminated |
| sp4 | Fixed opponents that keep spending (the "league") + 30% bot games | Still drifted |
| sp5 | Only winning pays (1st +2, 2nd 0, rest down to −1) | First run that didn't drift |
| sp6 | Two-sided spend term | Found the greedy-evaluation bug (plain argmax passed 68% of the time when the policy itself passed 29%); fixed with the two-step rule. 41% vs bots on fresh deals (iteration 600) |
| sp7 | Charge for each auction ended as the top spender | Eliminations 37% → 24% |
| sp8 / sp9 | End-of-game wasted-money charge; long exploration (temperature 1.6 → 1.0 over 600 iterations) | sp9 plateaued at ~48–49% vs bots on fresh deals; more multi-card bids in training (~1.2 cards per bid) |
| v2a | v2 network distilled from sp9, table themes, MCTS Medium opponents, 2,000 iterations | 47–50% vs MCTS Medium; ~13% of its bids add 2+ cards; shipped as `insane-v2` (iteration 1,900) |

`supervisor_notes.md` in each run folder is the running log: every review, analysis, decision and
reason, with dates.

## Known weaknesses of the shipped bot (fresh-deal analysis, v2a iteration 1,300)

- **It pays to dodge disgrace cards while well ahead and close to the elimination line.** In 13% of
  games it is eliminated despite leading on points, typically by a median $6.
- **Bidding wars over low paintings** (e.g. $26 for Painting 1), and occasional big jumps.
- **Loses on points with money unused** in about half its non-elimination losses.

The planned fix is a separate critic network (a value estimate per position, with its own weights,
never shared with the policy).
