"""
Distillation data for v2: positions from games played by the best v1 (sp9) networks, with the teacher's
FULL probability spread over all 1,024 moves at each decision, plus the return that decision went on to
get (same reward as training, rl_reward.decision_returns) for pre-training the critic.

    python v2/gen_distill.py --games 2000 --workers 3          (from .RL; writes runs_v2/distill/shard_*.npz)

Teachers: sp9 iterations 1000 and 1300 (49% / 48% wins on the 150 fresh deals). They play SAMPLED
(temperature 1), so the data covers the moves they consider -- multi-card bids included -- not only
their single favourite. Table mix per game: 3-5 seats; teacher seats everywhere except, in 40% of
games, 1-2 engine bots (MCTS medium, capped, or greedy -- never greedy alone), so positions against
real opponents are covered too. Only teacher decisions are recorded.
"""
import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

os.environ["DATABASE_URL"] = ""
RL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RL_DIR))

import numpy as np                                   # noqa: E402
import torch                                         # noqa: E402
import torch.nn.functional as F                      # noqa: E402

TEACHERS = ["runs/sp9/milestones/iter_001000.pt", "runs/sp9/milestones/iter_001300.pt"]
SEED0 = 300_000_000            # deck seeds: disjoint from training (100k+), checks (50M+) and analysis (60M-90M)
BOT_LINEUPS = [["medium"], ["medium", "capped"], ["capped"], ["capped", "greedy"], ["medium", "medium"]]


def _worker_init():
    torch.set_num_threads(1)
    try:
        os.nice(10)
    except OSError:
        pass


class RecordingTeacher:
    """rl_model.MyPolicy's sampling, but keeps (state, legal moves, probabilities) for every decision."""

    def __init__(self, net, seed):
        self.net, self.rng, self.log = net, np.random.default_rng(seed), []

    def bid(self, state, mask):
        import rl_seat as rs
        from rl_model import score_boards
        cands = rs.legal_bids(state)
        with torch.no_grad():
            p = F.softmax(score_boards(self.net, rs.afterstates(state, cands)).cpu().double(), 0).numpy()
        p /= p.sum()
        self.log.append((state.copy(), cands, p.astype(np.float32)))
        return rs.candidate_to_vector(cands[self.rng.choice(len(cands), p=p)])


def play_chunk(job):
    lo, hi, out_path = job
    import rl_seat as rs
    from rl_model import ScoreNet
    from rl_reward import decision_returns
    nets = []
    for path in TEACHERS:
        n = ScoreNet()
        n.load_state_dict(torch.load(RL_DIR / path, map_location="cpu"))
        nets.append(n.eval())
    X, P, G, NP, CARDS = [], [], [], [], []
    with rs._fast():
        for g in range(lo, hi):
            rng = np.random.default_rng(SEED0 + g)
            n = int(rng.choice([3, 4, 5]))
            seats = [RecordingTeacher(nets[int(rng.integers(len(nets)))], seed=SEED0 + g * 7 + s) for s in range(n)]
            policies = list(seats)
            if rng.random() < 0.4:
                lineup = BOT_LINEUPS[int(rng.integers(len(BOT_LINEUPS)))]
                for b, name in zip(rng.permutation(n)[:len(lineup)], lineup):
                    policies[int(b)] = name
            record, rl_seats = rs.play_selfplay(policies, seed=SEED0 + g)
            ledger = rs.auction_ledger(record)
            for s, pol in enumerate(policies):
                if isinstance(pol, str):
                    continue
                returns, _ = decision_returns(record, s, ledger)
                assert len(returns) == len(pol.log), (len(returns), len(pol.log))
                for (state, cands, p), g_t in zip(pol.log, returns):
                    dense = np.zeros(len(rs.SUBSETS), dtype=np.float16)
                    dense[cands] = p
                    X.append(state)
                    P.append(dense)
                    G.append(g_t)
                    NP.append(n)
                    CARDS.append(rs.SUBSET_SIZE[cands] @ p)        # expected cards added (for the report)
    np.savez_compressed(out_path, X=np.array(X, dtype=np.int8), P=np.array(P), G=np.array(G, dtype=np.float32),
                        n_players=np.array(NP, dtype=np.int8), exp_cards=np.array(CARDS, dtype=np.float32))
    return out_path, len(X)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default="runs_v2/distill")
    ap.add_argument("--chunk", type=int, default=50)
    a = ap.parse_args()
    out = RL_DIR / a.out
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(lo, min(lo + a.chunk, a.games), out / f"shard_{lo:06d}.npz")
            for lo in range(0, a.games, a.chunk) if not (out / f"shard_{lo:06d}.npz").exists()]   # resumable
    t0, total = time.time(), 0
    with Pool(a.workers, initializer=_worker_init) as pool:
        for i, (path, k) in enumerate(pool.imap_unordered(play_chunk, jobs), 1):
            total += k
            print(f"[{time.strftime('%H:%M:%S')}] {i}/{len(jobs)} shards, {total} decisions, "
                  f"{time.time() - t0:.0f}s", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
