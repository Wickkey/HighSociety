"""
Benchmark a model against MCTS bots on fresh deals: one seat for the model, every other seat an MCTS bot
(the lobby's Medium by default), 3/4/5 players rotating, the model's seat rotating, greedy play.

    nice -n 15 python v2/bench_medium.py <weights.pt> [n_games] [medium|hard|easy] [seed0]     (from .RL)

Deck seeds 85,000,000 + i: never used in training, checks or the other analyses. Works for v1 (ScoreNet)
and v2 (PolicyNet) weights. Prints win %, eliminated %, and finishing places, next to chance.
"""
import collections
import os
import sys
import time
from pathlib import Path

os.environ["DATABASE_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                      # noqa: E402
import torch                            # noqa: E402

import rl_seat as rs                    # noqa: E402
import train_v2                         # noqa: E402

SEED0 = 85_000_000


def main():
    path = sys.argv[1]
    n_games = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    kind = sys.argv[3] if len(sys.argv) > 3 else "medium"
    seed0 = int(sys.argv[4]) if len(sys.argv) > 4 else SEED0       # e.g. 88000000 for a confirmation set
    torch.set_num_threads(1)
    net = train_v2.load_any(path)
    wins = elim = 0
    chance = 0.0
    places = collections.Counter()
    t0 = time.time()
    for i in range(n_games):
        n = (3, 4, 5)[i % 3]
        seat = i % n
        record, _ = rs.play_game(train_v2.make_policy(net, greedy=True), opponents=[kind] * (n - 1), seat=seat,
                                 seed=seed0 + i)
        key = {s["seat"]: (s["eliminated"], -s["points"]) for s in record["standings"]}
        me = next(s for s in record["standings"] if s["seat"] == seat)
        wins += me["is_winner"]
        elim += me["eliminated"]
        chance += 1 / n
        places["eliminated" if key[seat][0] else sorted(key, key=key.get).index(seat) + 1] += 1
    order = sorted(places, key=lambda k: (k == "eliminated", k if k != "eliminated" else 0))
    names = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", "eliminated": "eliminated"}
    print(f"{path} vs MCTS {kind}, {n_games} fresh games (seeds {seed0}+): win {wins / n_games:.0%} (chance {chance / n_games:.0%}) | "
          f"eliminated {elim / n_games:.0%} | places " + ", ".join(f"{names[k]} {places[k] / n_games:.0%}" for k in order)
          + f" | {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
