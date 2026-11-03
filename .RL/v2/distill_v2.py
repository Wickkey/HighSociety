"""
Distil the v1 teachers (sp9) into the v2 network, and pre-train its critic.

    python v2/distill_v2.py                   (from .RL; reads runs_v2/distill/shard_*.npz)

Policy: cross-entropy between the teacher's full probability spread over the legal moves and v2's
masked softmax -- i.e. v2 learns how much sp9 likes EVERY legal move, multi-card bids included,
not just its single pick. Critic (value head): mean-squared error to the return each decision went
on to get (the same reward training uses), weighted by --value-weight.

Split by game shard: the last --test-frac of shards are held out. Reports, on held-out positions:
    agree      v2's greedy move (two-step rule, as in play) == the teacher's greedy move
    kl         KL(teacher || v2), in nats (0 = identical spreads)
    cards      expected cards added per decision, v2 vs teacher (multi-card tendency)
    value R2   share of the return's variance the critic explains (0 = no better than the mean)

Writes runs_v2/distill_v2.pt (state_dict incl. value head) and runs_v2/distill_v2.log.
"""
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

os.environ["DATABASE_URL"] = ""
RL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                                    # noqa: E402
import torch                                          # noqa: E402
import torch.nn.functional as F                       # noqa: E402

import model_v2 as m2                                 # noqa: E402
import rl_seat as rs                                  # noqa: E402

SIZE = torch.tensor(rs.SUBSET_SIZE, dtype=torch.float32)


def greedy_moves(probs, mask):
    """The two-step greedy rule (pass if P(pass) >= 0.5, else the most likely bid), batched."""
    bid_p = probs.masked_fill(~mask, -1.0).clone()
    bid_p[:, m2.PASS] = -1.0
    best_bid = bid_p.argmax(1)
    has_bid = mask[:, 1:].any(1)
    return torch.where((probs[:, m2.PASS] >= 0.5) | ~has_bid, torch.zeros_like(best_bid), best_bid)


def load(paths):
    d = [np.load(p) for p in paths]
    X = np.concatenate([x["X"] for x in d])
    P = np.concatenate([x["P"] for x in d]).astype(np.float32)
    G = np.concatenate([x["G"] for x in d])
    return X, P, G


def evaluate(net, X, P, G, mask, batch=2048):
    net.eval()
    agree, kl, cards_s, cards_t, vpred = 0, 0.0, 0.0, 0.0, []
    with torch.no_grad():
        for i in range(0, len(X), batch):
            x = torch.from_numpy(X[i:i + batch]).float()
            p = torch.from_numpy(P[i:i + batch])
            mk = torch.from_numpy(mask[i:i + batch])
            logits, v = net(x)
            logq = F.log_softmax(logits.masked_fill(~mk, float("-inf")), 1)
            q = logq.exp()
            kl += (p * (torch.log(p.clamp_min(1e-12)) - logq.masked_fill(~mk, 0))).sum().item()
            agree += (greedy_moves(q, mk) == greedy_moves(p, mk)).sum().item()
            cards_s += (q * SIZE).sum().item()
            cards_t += (p * SIZE).sum().item()
            vpred.append(v.numpy())
    vpred = np.concatenate(vpred)
    r2 = 1 - np.mean((vpred - G) ** 2) / np.var(G)
    n = len(X)
    return {"agree": agree / n, "kl": kl / n, "cards_v2": cards_s / n, "cards_teacher": cards_t / n, "value_r2": r2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="runs_v2/distill")
    ap.add_argument("--out", default="runs_v2/distill_v2.pt")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--value-weight", type=float, default=0.5)
    ap.add_argument("--test-frac", type=float, default=0.1)
    ap.add_argument("--threads", type=int, default=3)
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    torch.manual_seed(0)
    log_path = RL_DIR / Path(a.out).with_suffix(".log")
    log_f = open(log_path, "a")

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log_f.write(line + "\n")
        log_f.flush()

    shards = sorted(glob.glob(str(RL_DIR / a.data / "shard_*.npz")))
    n_test = max(1, int(len(shards) * a.test_frac))
    Xtr, Ptr, Gtr = load(shards[:-n_test])
    Xte, Pte, Gte = load(shards[-n_test:])
    t0 = time.time()
    Mtr, Mte = m2.legal_mask(Xtr), m2.legal_mask(Xte)
    log(f"data: {len(shards)} shards, {len(Xtr)} train / {len(Xte)} test positions (masks {time.time() - t0:.0f}s)")
    leak = (Ptr[~Mtr] > 1e-3).sum()
    assert leak == 0, f"teacher puts probability on {leak} moves the v2 mask calls illegal"

    net = m2.PolicyNet(value_head=True)
    log(f"PolicyNet: {sum(q.numel() for q in net.parameters())} params")
    opt = torch.optim.Adam(net.parameters(), lr=a.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.epochs)
    rng = np.random.default_rng(0)
    log(f"before training: test {json.dumps({k: round(v, 3) for k, v in evaluate(net, Xte, Pte, Gte, Mte).items()})}")
    for ep in range(a.epochs):
        net.train()
        t0, tot_pol, tot_val, nb = time.time(), 0.0, 0.0, 0
        for idx in np.array_split(rng.permutation(len(Xtr)), max(1, len(Xtr) // a.batch)):
            x = torch.from_numpy(Xtr[idx]).float()
            p = torch.from_numpy(Ptr[idx])
            mk = torch.from_numpy(Mtr[idx])
            logits, v = net(x)
            logq = F.log_softmax(logits.masked_fill(~mk, float("-inf")), 1).masked_fill(~mk, 0)
            pol = -(p * logq).sum(1).mean()
            val = F.mse_loss(v, torch.from_numpy(Gtr[idx]))
            loss = pol + a.value_weight * val
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot_pol, tot_val, nb = tot_pol + pol.item(), tot_val + val.item(), nb + 1
        sched.step()
        te = evaluate(net, Xte, Pte, Gte, Mte)
        log(f"epoch {ep + 1}/{a.epochs}: train policy CE {tot_pol / nb:.3f} value MSE {tot_val / nb:.3f} | "
            f"test agree {te['agree']:.1%} KL {te['kl']:.3f} cards v2 {te['cards_v2']:.2f} vs teacher "
            f"{te['cards_teacher']:.2f} value R2 {te['value_r2']:.2f} | {time.time() - t0:.0f}s")
        torch.save(net.state_dict(), RL_DIR / a.out)
    tr = evaluate(net, Xtr[:20000], Ptr[:20000], Gtr[:20000], Mtr[:20000])
    log(f"final: train agree {tr['agree']:.1%} KL {tr['kl']:.3f} | test agree {te['agree']:.1%} KL {te['kl']:.3f} "
        f"(a big train/test gap = memorising; low agreement on both = network too small) -> {a.out}")


if __name__ == "__main__":
    main()
