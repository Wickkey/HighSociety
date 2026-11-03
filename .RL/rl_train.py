"""
Self-play REINFORCE training as a script (safe for long runs), with checkpoints and progress checks.

    python rl_train.py --run runs/sp1                  # start from runs/imitation.pt (bot-imitation warm start)
    python rl_train.py --run runs/sp1 --resume         # continue from runs/sp1/latest.pt
    python rl_train.py --run runs/sp2 --init none      # start from a fresh, untrained network

runs/imitation.pt is written by section 4b of RL_exploration.ipynb.

Everything goes into the run folder:
    config.json     the settings of this run
    train.log       one line per iteration + every progress check (tail -f it, or read it from the notebook)
    metrics.jsonl   the same, one JSON object per line ("kind": "iter" or "eval")
    latest.pt       full training state; saved every --checkpoint-every iterations and on exit / Ctrl-C / kill
    milestones/iter_000000.pt ...   frozen network weights at every progress check

Exploration: the sampling temperature and the entropy bonus start high (--temp-start, --entropy-start)
and fall linearly to --temp-end / --entropy-end over the first --explore-iters iterations. The training
update uses the same temperature the moves were sampled with, so it stays on-policy.

Progress check, every --eval-every iterations (all opponents are fast -- no MCTS), current network
playing greedily:
    vs_heuristics   one seat vs engine bots (all capped, or capped + greedy): a fixed yardstick
    vs_start        one seat vs the iteration-0 network in every other seat: "better than where it began?"
    vs_prev         one seat vs the previous check's network: "still improving lately?"
Each reports a win rate next to chance (1 / players). The same deck seeds are used every check, so
numbers are comparable over time.

best.pt holds the weights with the highest vs_heuristics win rate so far. The run stops by itself
(exit 0, status "early_stop") when vs_heuristics is below --stop-below x chance (default half of
chance, ~13%) for --stop-after-bad checks in a row.

Training games are self-play (current network + frozen past copies); with chance --p-heuristic-game
1-2 seats are engine bots instead (capped only, or capped + greedy; never greedy alone), so habits
that only work against itself get punished. Kept occasional so it doesn't learn to beat those bots.
--league adds fixed opponents (earlier checkpoints) that are never replaced: runs sp2/sp3 drifted into
"everyone hoards", where whoever spends is the poorest and gets eliminated; opponents that keep spending
break that.
"""
import os
os.environ["DATABASE_URL"] = ""          # never touch the real DB ("" -- an absent key gets refilled by dotenv)

import argparse
import copy
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import torch
import torch.nn.functional as F

import rl_seat as rs
import state_encoding as se
from dashboard import write_dashboard
from rl_model import DEVICE, MyPolicy, ScoreNet, score_boards
from rl_reward import TERMS, decision_returns

RESTART_CODE = 3             # exit code: "resume me now" (run_training.sh restarts at once, not counted as a crash)
TRAIN_SEED0 = 100_000        # deck seeds for training games (seed0 + game number)
EVAL_SEED0 = 50_000_000      # deck seeds for progress checks: fixed, never used in training


# ---------------------------------------------------------------- the update

def log_probs(net, steps, temperature=1.0):
    """steps: list of (state, chosen candidate). Returns (log-prob of each choice, entropy of each turn)."""
    cands = [rs.legal_bids(s) for s, _ in steps]
    scores = score_boards(net, np.concatenate([rs.afterstates(s, c) for (s, _), c in zip(steps, cands)]))
    scores = scores / temperature
    logp, ent = [], []
    for seg, c, (_, chosen) in zip(torch.split(scores, [len(c) for c in cands]), cands, steps):
        lp = F.log_softmax(seg, dim=0)
        logp.append(lp[int(np.flatnonzero(c == chosen)[0])])
        ent.append(-(lp.exp() * lp).sum())
    return torch.stack(logp), torch.stack(ent)


def money_left(record, seat):
    """(seat's money left at game end, the poorest OTHER player's money left), out of $106 each."""
    st = record["standings"]
    return (next(s["money_left"] for s in st if s["seat"] == seat),
            min(s["money_left"] for s in st if s["seat"] != seat))


def schedule(start, end, it, span):
    """Linear from start (iteration 0) to end (iteration `span` and after)."""
    if span <= 0:
        return end
    return start + (end - start) * min(it / span, 1.0)


class Trainer:
    def __init__(self, args):
        self.args = args
        self.player_counts = tuple(int(n) for n in args.player_counts.split(","))
        self.net = ScoreNet().to(DEVICE)
        self.opt = torch.optim.Adam(self.net.parameters(), lr=args.lr)
        self.rng = np.random.default_rng(args.seed)
        self.pool = []           # frozen past copies the learner plays against (rolling: the latest few)
        self.league = [self.frozen(torch.load(p, map_location=DEVICE))   # fixed opponents, never replaced
                       for p in args.league.split(",") if p]
        self.baseline = {}       # running average return, per table size
        self.it = 0              # iterations done
        self.games = 0           # training games played (deck seed counter)
        self.best = None         # best vs_heuristics win rate so far (best.pt holds those weights)
        self.bad_checks = 0      # consecutive progress checks with vs_heuristics below chance

    # ---- checkpoints
    def state_dict(self):
        return {"net": self.net.state_dict(), "opt": self.opt.state_dict(),
                "pool": [p.state_dict() for p in self.pool], "baseline": self.baseline,
                "it": self.it, "games": self.games, "rng": self.rng.bit_generator.state,
                "best": self.best, "bad_checks": self.bad_checks, "args": vars(self.args)}

    def save(self, path):
        tmp = str(path) + ".tmp"
        torch.save(self.state_dict(), tmp)
        os.replace(tmp, path)    # atomic: a kill mid-save never leaves a half-written latest.pt

    def load(self, path):
        st = torch.load(path, map_location=DEVICE, weights_only=False)
        self.net.load_state_dict(st["net"])
        self.opt.load_state_dict(st["opt"])
        self.pool = [self.frozen(sd) for sd in st["pool"]]
        self.baseline, self.it, self.games = st["baseline"], st["it"], st["games"]
        self.rng.bit_generator.state = st["rng"]
        self.best, self.bad_checks = st.get("best"), st.get("bad_checks", 0)

    def opponent(self):
        """A non-learner seat's network: a fixed league member with chance --p-league, else a recent snapshot."""
        use_league = self.league and (not self.pool or self.rng.random() < self.args.p_league)
        group = self.league if use_league else self.pool
        return group[int(self.rng.integers(len(group)))]

    @staticmethod
    def frozen(state_dict):
        net = ScoreNet().to(DEVICE)
        net.load_state_dict(state_dict)
        return net.eval()

    # ---- one training iteration
    def train_iter(self):
        a, t0 = self.args, time.time()
        temperature = schedule(a.temp_start, a.temp_end, self.it, a.explore_iters)
        entropy_coef = schedule(a.entropy_start, a.entropy_end, self.it, a.explore_iters)
        steps, advs, n_cards = [], [], []
        money, poorest, elim = [], [], []       # learner seats: money left at the end, poorest other's, eliminated?
        rewards = {n: [] for n in self.player_counts}
        returns = {n: [] for n in self.player_counts}
        parts_all = {k: [] for k in (*TERMS, "rank", "waste")}
        for _ in range(a.games_per_iter):
            n = int(self.rng.choice(self.player_counts))
            have_opponents = bool(self.pool or self.league)
            learner = [not have_opponents or self.rng.random() >= a.p_snapshot for _ in range(n)]
            if not any(learner):
                learner[int(self.rng.integers(n))] = True
            policies = [MyPolicy(self.net if learner[s] else self.opponent(),
                                 greedy=False, temperature=temperature, seed=TRAIN_SEED0 + self.games * 5 + s)
                        for s in range(n)]
            if self.rng.random() < a.p_heuristic_game:     # now and then: engine bots in 1-2 seats
                # capped only (1 or 2 of them), or capped + greedy together -- never greedy alone
                bots = [["capped"], ["capped", "capped"], ["capped", "greedy"]][int(self.rng.integers(3))]
                for b, name in zip(self.rng.permutation(n)[:len(bots)], bots):
                    learner[int(b)], policies[int(b)] = False, name
                if not any(learner):                       # keep at least one learner seat
                    s = next(s for s in range(n) if not isinstance(policies[s], str))
                    learner[s] = True
                    policies[s] = MyPolicy(self.net, greedy=False, temperature=temperature,
                                           seed=TRAIN_SEED0 + self.games * 5 + s)
            record, seats = rs.play_selfplay(policies, seed=TRAIN_SEED0 + self.games)
            self.games += 1
            ledger = rs.auction_ledger(record)
            for s in range(n):
                if not learner[s]:
                    continue
                G, parts = decision_returns(record, s, ledger)
                rewards[n].append(sum(parts.values()))
                m, p = money_left(record, s)
                money.append(m)
                poorest.append(p)
                elim.append(next(x["eliminated"] for x in record["standings"] if x["seat"] == s))
                returns[n].extend(G)
                for k, v in parts.items():
                    parts_all[k].append(v)
                for (state, _mask, out, _move), g_t in zip(seats[s].decisions, G):
                    steps.append((state, rs.vector_to_candidate(out)))
                    advs.append(g_t - self.baseline.get(n, g_t))
                    if not out[rs.PASS]:
                        n_cards.append(int(out[:se.N_MONEY].sum()))
        for n, r_n in returns.items():
            if r_n:
                m = float(np.mean(r_n))
                self.baseline[n] = m if n not in self.baseline else 0.9 * self.baseline[n] + 0.1 * m

        # gradient accumulated in chunks of <= max_boards candidate boards (GPU memory)
        sizes = [len(rs.legal_bids(s)) for s, _ in steps]
        bounds, start, total = [], 0, 0
        for i, k in enumerate(sizes):
            if total + k > a.max_boards:
                bounds.append((start, i))
                start, total = i, 0
            total += k
        bounds.append((start, len(steps)))
        self.opt.zero_grad()
        adv_t, ent_sum, loss_sum = torch.tensor(advs, dtype=torch.float32, device=DEVICE), 0.0, 0.0
        for lo, hi in bounds:
            logp, ent = log_probs(self.net, steps[lo:hi], temperature)
            loss = (-(adv_t[lo:hi] * logp).sum() - entropy_coef * ent.sum()) / len(steps)
            loss.backward()
            ent_sum += ent.sum().item()
            loss_sum += loss.item()
        grads_ok = all(torch.isfinite(q.grad).all() for q in self.net.parameters() if q.grad is not None)
        skipped = not (np.isfinite(loss_sum) and grads_ok)
        if skipped:                       # a non-finite loss/gradient would wreck the weights: skip this update
            self.opt.zero_grad()
        else:
            self.opt.step()

        self.it += 1
        if self.it % a.snapshot_every == 0:
            self.pool.append(copy.deepcopy(self.net).eval())
            self.pool = self.pool[-a.max_snapshots:]
        return {"kind": "iter", "it": self.it, "games": self.games, "seconds": round(time.time() - t0, 1),
                "temperature": round(temperature, 3), "entropy_coef": round(entropy_coef, 4),
                "decisions": len(steps), "entropy": round(ent_sum / len(steps), 3),
                "cards_per_bid": round(float(np.mean(n_cards)), 2) if n_cards else None,
                "money_left": round(float(np.mean(money)), 1),
                "poorest_other_money_left": round(float(np.mean(poorest)), 1),
                "eliminated": round(float(np.mean(elim)), 3),
                "reward": {n: round(float(np.mean(v)), 3) for n, v in rewards.items() if v},
                "terms": {k: round(float(np.mean(v)), 4) for k, v in parts_all.items() if v},
                "snapshots": len(self.pool), "skipped_update": skipped}

    # ---- progress checks
    def _sizes(self, i):
        return self.player_counts[i % len(self.player_counts)]

    def eval_vs_heuristics(self, n_games):
        wins, chance, pts, elim, money, poorest = 0, 0.0, 0.0, 0, 0.0, 0.0
        for i in range(n_games):
            n = self._sizes(i)
            # opponents: all capped, or a capped + greedy mix (at least one of each) -- never greedy alone
            r = np.random.default_rng(i)
            opp = ["capped"] * (n - 1) if r.random() < 0.5 else \
                [str(o) for o in r.permutation(["capped", "greedy"] + list(r.choice(["capped", "greedy"], n - 3)))]
            seat = i % n
            record, _ = rs.play_game(MyPolicy(self.net, greedy=True), opponents=opp, seat=seat,
                                     seed=EVAL_SEED0 + i)
            me = next(s for s in record["standings"] if s["seat"] == seat)
            wins += me["is_winner"]
            elim += me["eliminated"]
            pts += me["points"]
            m, p = money_left(record, seat)
            money += m
            poorest += p
            chance += 1 / n
        return {"win_rate": round(wins / n_games, 3), "chance": round(chance / n_games, 3),
                "eliminated": round(elim / n_games, 3), "avg_points": round(pts / n_games, 2),
                "avg_money_left": round(money / n_games, 1),           # of $106; all-pass ends near 106
                "poorest_other_money_left": round(poorest / n_games, 1)}

    def eval_vs_past(self, past, n_games):
        wins, chance, elim, money, poorest = 0, 0.0, 0, 0.0, 0.0
        for i in range(n_games):
            n = self._sizes(i)
            seat = i % n
            policies = [MyPolicy(self.net if s == seat else past, greedy=True) for s in range(n)]
            record, _ = rs.play_selfplay(policies, seed=EVAL_SEED0 + 1_000_000 + i)
            me = next(s for s in record["standings"] if s["seat"] == seat)
            wins += me["is_winner"]
            elim += me["eliminated"]
            m, p = money_left(record, seat)
            money += m
            poorest += p
            chance += 1 / n
        return {"win_rate": round(wins / n_games, 3), "chance": round(chance / n_games, 3),
                "eliminated": round(elim / n_games, 3), "avg_money_left": round(money / n_games, 1),
                "poorest_other_money_left": round(poorest / n_games, 1)}


# ---------------------------------------------------------------- main loop

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", required=True, help="run folder, e.g. runs/sp1")
    p.add_argument("--resume", action="store_true", help="continue from <run>/latest.pt")
    p.add_argument("--init", default="runs/imitation.pt",
                   help="starting weights (default: the bot-imitation warm start); 'none' = untrained network")
    p.add_argument("--iters", type=int, default=2000, help="stop after this many iterations in total")
    p.add_argument("--games-per-iter", type=int, default=20)
    p.add_argument("--player-counts", default="3,4,5")
    p.add_argument("--p-snapshot", type=float, default=0.5, help="chance a seat is a frozen past copy")
    p.add_argument("--league", default="",
                   help="comma-separated weight files kept as permanent opponents (e.g. earlier checkpoints "
                        "that still spend money), so self-play can't settle into everyone hoarding")
    p.add_argument("--p-league", type=float, default=0.5,
                   help="chance a non-learner seat is a league member instead of a recent snapshot")
    p.add_argument("--p-heuristic-game", type=float, default=0.2,
                   help="chance a training game seats engine bots: 1-2 capped, or capped + greedy")
    p.add_argument("--snapshot-every", type=int, default=5)
    p.add_argument("--max-snapshots", type=int, default=10)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--temp-start", type=float, default=2.0, help="sampling temperature at iteration 0")
    p.add_argument("--temp-end", type=float, default=1.0)
    p.add_argument("--entropy-start", type=float, default=0.05, help="entropy bonus at iteration 0")
    p.add_argument("--entropy-end", type=float, default=0.01)
    p.add_argument("--explore-iters", type=int, default=300, help="iterations to go from *-start to *-end")
    p.add_argument("--max-boards", type=int, default=8192, help="candidate boards per gradient chunk")
    p.add_argument("--checkpoint-every", type=int, default=25)
    p.add_argument("--eval-every", type=int, default=25)
    p.add_argument("--eval-games-heuristics", type=int, default=60)
    p.add_argument("--eval-games-past", type=int, default=45)
    p.add_argument("--stop-after-bad", type=int, default=3,
                   help="stop when vs_heuristics is below --stop-below x chance for this many checks in a row "
                        "(0 = never)")
    p.add_argument("--stop-below", type=float, default=0.5,
                   help="auto-stop threshold as a fraction of chance (0.5 = half of chance, ~13%%)")
    p.add_argument("--restart-every", type=int, default=50,
                   help="exit for a lossless resume every N iterations, to free leaked GPU-backend memory (0 = never)")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    run = Path(args.run)
    (run / "milestones").mkdir(parents=True, exist_ok=True)
    log_file = open(run / "train.log", "a")
    metrics_file = open(run / "metrics.jsonl", "a")

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log_file.write(line + "\n")
        log_file.flush()

    def metric(m):
        metrics_file.write(json.dumps(m) + "\n")
        metrics_file.flush()

    def dashboard():
        try:
            write_dashboard(run)
        except Exception as e:                # the dashboard must never take training down
            log(f"dashboard not written: {e!r}")

    def status(event, **extra):
        metric({"kind": "status", "event": event, "it": tr.it, "time": time.time(), **extra})
        dashboard()

    def setup_error(msg):
        print(msg, file=sys.stderr)
        sys.exit(2)                           # 2 = setup problem: run_training.sh does not retry it

    torch.manual_seed(args.seed)
    tr = Trainer(args)
    if args.resume:
        tr.load(run / "latest.pt")
        cfg_path = run / "config.json"
        old_cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        changed = {k: (old_cfg.get(k), v) for k, v in vars(args).items() if k != "resume" and old_cfg.get(k) != v}
        json.dump(vars(args), open(cfg_path, "w"), indent=1)      # the settings now in effect (dashboard reads this)
        log(f"resumed {run}/latest.pt at iteration {tr.it} ({tr.games} games), device {DEVICE}"
            + ("; settings changed: " + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in changed.items()) if changed else ""))
        status("resume")
    else:
        if (run / "latest.pt").exists():
            setup_error(f"{run}/latest.pt already exists -- use --resume, or pick a new --run folder")
        if args.init.lower() != "none":
            if not Path(args.init).exists():
                setup_error(f"{args.init} not found -- run section 4b of RL_exploration.ipynb to create it, "
                            f"or pass --init none to start from an untrained network")
            tr.net.load_state_dict(torch.load(args.init, map_location=DEVICE))
        json.dump(vars(args), open(run / "config.json", "w"), indent=1)
        log(f"new run {run}: init {args.init}, device {DEVICE}, {sum(q.numel() for q in tr.net.parameters())} params")
        status("start")

    # a plain `kill` (SIGTERM) would skip the final save -- turn it into a normal exit instead
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    def milestone_path(it):
        return run / "milestones" / f"iter_{it:06d}.pt"

    def progress_check():
        t0 = time.time()
        torch.save(tr.net.state_dict(), milestone_path(tr.it))
        res = {"kind": "eval", "it": tr.it, "games": tr.games,
               "vs_heuristics": tr.eval_vs_heuristics(args.eval_games_heuristics)}
        start = sorted((run / "milestones").glob("iter_*.pt"))[0]
        if tr.it > 0:
            res["vs_start"] = tr.eval_vs_past(tr.frozen(torch.load(start, map_location=DEVICE)),
                                              args.eval_games_past)
            prev = milestone_path(tr.it - args.eval_every)
            if prev.exists() and prev != start:
                res["vs_prev"] = tr.eval_vs_past(tr.frozen(torch.load(prev, map_location=DEVICE)),
                                                 args.eval_games_past)
        if DEVICE.type == "mps":
            torch.mps.empty_cache()
        res["seconds"] = round(time.time() - t0, 1)
        metric(res)
        parts = [f"{k} {v['win_rate']:.0%} (chance {v['chance']:.0%})" for k, v in res.items()
                 if isinstance(v, dict)]
        vh = res["vs_heuristics"]
        log(f"CHECK it {tr.it}: " + " | ".join(parts) +
            f" | eliminated {vh['eliminated']:.0%} | money left ${vh['avg_money_left']:.0f}"
            f" (poorest bot ${vh['poorest_other_money_left']:.0f}) | {res['seconds']}s")
        if tr.best is None or vh["win_rate"] > tr.best:
            tr.best = vh["win_rate"]
            torch.save(tr.net.state_dict(), run / "best.pt")
            log(f"new best vs_heuristics {tr.best:.0%} at iteration {tr.it} -> {run}/best.pt")
        tr.bad_checks = tr.bad_checks + 1 if vh["win_rate"] < args.stop_below * vh["chance"] else 0
        dashboard()
        return args.stop_after_bad > 0 and tr.bad_checks >= args.stop_after_bad

    def weights_ok():
        return all(torch.isfinite(q).all() for q in tr.net.parameters())

    # Exit codes (run_training.sh relies on them): 0 = finished or stopped by hand, 1 = crashed (retry), 3 = planned restart,
    # 2 = setup problem (no retry).
    try:
        if tr.it == 0:
            progress_check()                  # where it starts
        while tr.it < args.iters:
            m = tr.train_iter()
            if DEVICE.type == "mps":
                # The Mac GPU's allocator caches every batch size it has seen and never gives the memory
                # back; run sp6 reached an 18 GB footprint on an 8 GB Mac (heavy swapping, ~5x slower).
                torch.mps.empty_cache()
                m["gpu_mb"] = round(torch.mps.driver_allocated_memory() / 2**20)
            metric(m)
            rew = " ".join(f"{n}p {v:+.2f}" for n, v in m["reward"].items())
            terms = " ".join(f"{k} {v:+.3f}" for k, v in m["terms"].items())
            log(f"iter {m['it']}: reward {rew} | {terms} | T {m['temperature']:.2f} | "
                f"entropy {m['entropy']:.2f} | cards/bid {m['cards_per_bid']} | money left ${m['money_left']:.0f} "
                f"(poorest other ${m['poorest_other_money_left']:.0f}) elim {m['eliminated']:.0%} | "
                f"{m['decisions']} dec | {m['seconds']}s"
                + (" | UPDATE SKIPPED (non-finite loss/gradient)" if m["skipped_update"] else ""))
            if tr.it % args.checkpoint_every == 0:
                tr.save(run / "latest.pt")
            if tr.it % args.eval_every == 0:
                if progress_check():
                    tr.save(run / "latest.pt")
                    log(f"early stop at iteration {tr.it}: vs_heuristics below {args.stop_below:g} x chance for {tr.bad_checks} "
                        f"checks in a row; best weights ({tr.best:.0%}) are in {run}/best.pt")
                    status("early_stop")
                    return
            else:
                dashboard()
            if args.restart_every and tr.it % args.restart_every == 0 and tr.it < args.iters:
                # PyTorch 2.2's Mac-GPU backend leaks native memory (~40-150 MB per iteration; sp6 reached an
                # 18 GB footprint on an 8 GB Mac and slowed ~5x). Not freeable from Python, so: save, exit with
                # RESTART_CODE, and run_training.sh resumes at once -- lossless (weights, optimiser, pool, rng).
                tr.save(run / "latest.pt")
                log(f"restarting at iteration {tr.it} to free memory (every {args.restart_every} iterations)")
                return RESTART_CODE
        tr.save(run / "latest.pt")
        log(f"finished: {tr.it} iterations, saved {run}/latest.pt")
        status("finish")
    except (KeyboardInterrupt, SystemExit):   # Ctrl-C, or `kill` (SIGTERM -> sys.exit above)
        tr.save(run / "latest.pt")
        log(f"stopped by hand at iteration {tr.it}, saved {run}/latest.pt")
        status("stopped")
        sys.exit(0)
    except Exception as e:
        import traceback
        log("CRASH:\n" + traceback.format_exc())
        if weights_ok():                      # never checkpoint broken weights over a good latest.pt
            tr.save(run / "latest.pt")
            log(f"saved {run}/latest.pt at iteration {tr.it}")
        status("crash", error=repr(e)[:300])
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main() or 0)
