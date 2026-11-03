"""
Looks for decisions a strong human would question: plays games (greedy) and flags moves by simple rules.

    nice -n 15 python v2/odd_moves.py <weights.pt> [n_games] [medium|bots]     (from .RL)

Flags (each with the situation printed):
  overshoot      raised to far more than the cheapest legal bid that beats the high bid (>= $8 and >= 2x the
                 extra needed)
  extra_card     a multi-card bid where dropping one of the added cards would still beat the high bid
  pricey_small   bid more than $4 per point for a painting worth <= 4 before the 3rd green card
  dodge_rich     raised in a disgrace auction while leading on points by >= 8 and holding <= $5 more
                 than the poorest other player (dodging pushes it toward elimination)
  passed_cheap   passed (without bidding) on a painting in the last stretch (3 greens out) when it could
                 beat the bid with <= $1.5/point and still stay $5+ above the poorest other player
  deeper_poor    bid late (3 greens out) while already the poorest player and leading on points
"""
import collections
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                      # noqa: E402
import torch                            # noqa: E402

import model_v2 as m2                   # noqa: E402
import rl_seat as rs                    # noqa: E402
import state_encoding as se             # noqa: E402
import train_v2                         # noqa: E402

MONEY = np.array(se.MONEY)
GREEN_ROWS = [se.PRESTIGE0, se.PRESTIGE0 + 1, se.PRESTIGE0 + 2, se.SCANDALE]


def describe(state):
    """Readable facts about a decision point."""
    g = se.GAME_COL
    cur = int(np.flatnonzero(state[se.N_MONEY:, g] == 2)[0]) + se.N_MONEY
    name = se.ROW_NAMES[cur]
    greens = sum(int(state[r, g] == 0 or state[r, g] == 2) for r in GREEN_ROWS)
    left = int((state[se.N_MONEY:, g] == 1).sum())
    n = int(sum(state[:se.N_MONEY, c].any() or state[se.N_MONEY:, c].any() for c in range(5)))
    hand = MONEY[state[:se.N_MONEY, 0] == 1]
    spent = [106 - int(MONEY[state[:se.N_MONEY, c] == 1].sum()) - int(MONEY[state[:se.N_MONEY, c] == -1].sum())
             for c in range(n)]                       # money gone for good, per seat (me = 0)
    money = [106 - s for s in spent]                  # money not yet spent (hand + on the table)
    table = [int(MONEY[state[:se.N_MONEY, c] == -1].sum()) for c in range(5)]

    def pts(c):
        own = state[se.N_MONEY:, c] == 1
        p = sum(v for v in range(1, 11) if state[se.PAINT0 + v - 1, c] == 1)
        p -= 5 * int(state[se.PASSE, c] == 1)
        p *= 2 ** int(sum(state[se.PRESTIGE0 + k, c] == 1 for k in range(3)))
        return p / 2 if state[se.SCANDALE, c] == 1 else p
    points = [pts(c) for c in range(n)]
    return dict(card=name, cur=cur, greens=greens, left=left, n=n, hand=list(hand), money=money,
                mine=table[0], top=max(table), points=points)


def main():
    path = sys.argv[1]
    n_games = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    opp_kind = sys.argv[3] if len(sys.argv) > 3 else "medium"
    torch.set_num_threads(1)
    net = train_v2.load_any(path)
    flags = collections.defaultdict(list)
    n_dec = 0

    class Spy(m2.PolicyV2):
        def choose(self, state):
            nonlocal n_dec
            mv = super().choose(state)
            n_dec += 1
            d = describe(state)
            cands = rs.legal_bids(state)
            bids = cands[cands != rs.PASS_CAND]
            need = d["top"] - d["mine"] + 1                    # extra money needed to lead
            card_val = int(d["card"].split()[-1]) if d["card"].startswith("Painting") else None
            me_poorest = d["money"][0] < min(d["money"][1:])
            lead = d["points"][0] - max(d["points"][1:])
            margin = d["money"][0] - min(d["money"][1:])
            disgrace = d["cur"] >= se.FAUX_PAS
            ctx = (f"{d['card']:<12s} greens {d['greens']} left {d['left']:2d} | high bid ${d['top']} (mine on table "
                   f"${d['mine']}) | my money ${d['money'][0]} vs poorest other ${min(d['money'][1:])} | points "
                   f"{d['points'][0]:g} vs best other {max(d['points'][1:]):g} | hand {d['hand']}")
            if mv != rs.PASS_CAND:
                added = list(MONEY[rs.SUBSETS[mv] == 1])
                cheapest = int(min(rs.SUBSET_VALUE[bids])) if len(bids) else None
                if sum(added) >= need + 8 and sum(added) >= 2 * cheapest:
                    flags["overshoot"].append(f"added {added} (=${sum(added)}) where ${cheapest} would do | {ctx}")
                if len(added) >= 2 and any(sum(added) - a >= need for a in added):
                    flags["extra_card"].append(f"added {added} -- one card fewer still beats the bid | {ctx}")
                if card_val and card_val <= 4 and d["greens"] < 3 and (d["mine"] + sum(added)) > 4 * card_val:
                    flags["pricey_small"].append(f"bid ${d['mine'] + sum(added)} total for it | {ctx}")
                if disgrace and lead >= 8 and margin <= 5:
                    flags["dodge_rich"].append(f"raised to ${d['mine'] + sum(added)} to dodge it | {ctx}")
                if d["greens"] >= 3 and me_poorest and lead > 0:
                    flags["deeper_poor"].append(f"added {added} | {ctx}")
            elif card_val and d["greens"] >= 3 and d["mine"] == 0 and len(bids):
                cheapest = int(min(rs.SUBSET_VALUE[bids]))
                if cheapest <= 1.5 * card_val and margin - cheapest >= 5:
                    flags["passed_cheap"].append(f"passed; ${cheapest} would have led | {ctx}")
            return mv

    for i in range(n_games):
        n = (3, 4, 5)[i % 3]
        seat = i % n
        r = np.random.default_rng(5000 + i)
        opp = [opp_kind] * (n - 1) if opp_kind != "bots" else (["capped"] * (n - 1) if r.random() < 0.5 else
              [str(o) for o in r.permutation(["capped", "greedy"] + list(r.choice(["capped", "greedy"], n - 3)))])
        rs.play_game(Spy(net, greedy=True), opponents=opp, seat=seat, seed=86_000_000 + i)
    print(f"{path} vs {opp_kind}: {n_games} games, {n_dec} decisions")
    for k in ("overshoot", "extra_card", "pricey_small", "dodge_rich", "passed_cheap", "deeper_poor"):
        v = flags[k]
        print(f"\n[{k}] {len(v)} times ({len(v) / n_games:.2f} per game)")
        for line in v[:4]:
            print("   ", line)


if __name__ == "__main__":
    main()
