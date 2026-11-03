"""
The reward (see section 7a of RL_exploration.ipynb for the reasoning).

    decision_returns(record, seat)  -> (return of each of seat's decisions, breakdown by term)
    reward(record, seat)            -> total reward of the whole game

Shared by RL_exploration.ipynb and rl_train.py, so edits here apply to both.
"""
import numpy as np

import rl_seat as rs


# ---- knobs ----
RANK_WIN, RANK_SECOND, RANK_LAST = 2.0, 0.0, -1.0
# 1st = RANK_WIN; 2nd ... last spread evenly from RANK_SECOND down to RANK_LAST -- only winning pays:
#   3p +2 0 -1 | 4p +2 0 -.5 -1 | 5p +2 0 -.33 -.67 -1
# history: sp1 middle places +0.05..+0.01 (learned to never bid); sp2 +1 ... -1 evenly; sp3/sp4 that with +1 for 1st
# (was: every middle place +0.05..+0.01 -- run sp1 learned to never bid, since hoarding money means you
#  are never the poorest, so never last, and that small positive beat the risk of bidding)
K_SPEND  = 0.02                # per $, x danger: penalty when my lead as top spender grows,
                               # reward for spending while staying at/below the top other spender
K_TOP    = 0.01                # per $ of my lead, x danger, for EVERY auction I end as the top spender
                               # (the top spender is the poorest = the one eliminated). Run sp6 was top
                               # spender for the last ~7 auctions of its eliminated games: K_SPEND only
                               # charges a GROWING lead, so sitting on one cost nothing until the final -1.
K_WASTE  = 0.005               # at game end, per $ left beyond what it took to stay above the poorest other
                               # player (+$1): money left at the end is worth nothing except for not being
                               # the poorest. sp7 lost on points with >= $20 unused in ~55% of those games and
                               # let cheap late paintings go. Small next to -1 for being eliminated. (From sp8.)
K_POINTS = 0.006               # per point of score gained / lost
TOTAL_MONEY, TOTAL_PAINTING_POINTS = 106, 55   # each player's starting money; all 10 paintings
GAMMA    = 1.0                 # discount per auction (1 = later auctions count fully)
TERMS = ("spend", "top_spender", "points", "made_pay")


def k_money(n_players):
    """
    Reward per $ other players paid because of me, from the game's fixed totals (conservative):
    all the money at the table (106 per player) chases the same 55 painting points, so
    $1 ~ 55 / (106 * n) points -> $5.8 per point at 3 players, $7.7 at 4, $9.6 at 5.
    """
    return K_POINTS * TOTAL_PAINTING_POINTS / (TOTAL_MONEY * n_players)


def danger(greens, left):
    """How close the end is: 1 / (1 + expected auctions left before the 4th green card)."""
    expected_left = (4 - greens) * (left + 1) / (5 - greens) - 1
    return 1 / (1 + max(expected_left, 0))


def rank_reward(record, seat):
    st = record["standings"]
    n = len(st)
    table = [RANK_WIN] + list(np.linspace(RANK_SECOND, RANK_LAST, n - 1))
    key = {s["seat"]: (s["eliminated"], -s["points"]) for s in st}      # eliminated last, then by points
    order = sorted(key, key=key.get)
    tied = [i for i, s in enumerate(order) if key[s] == key[seat]]
    return float(np.mean([table[i] for i in tied]))


def auction_rewards(ledger, seat):
    """(n_auctions, len(TERMS)) array: the spend / top_spender / points / made_pay reward of each auction for `seat`."""
    out = []
    for r in ledger["rounds"]:
        K_MONEY = k_money(len(r["spent_after"]))
        def lead(spent):
            return max(0, spent[seat] - max(v for s, v in spent.items() if s != seat))
        growth = max(0, lead(r["spent_after"]) - lead(r["spent_before"]))
        # the top spender is the poorest player (the one eliminated), so the best place is just below
        # them: money spent up to the top other spender's total is rewarded, a growing lead is penalised
        top_other = max(v for s, v in r["spent_after"].items() if s != seat)
        used = max(0, min(r["spent_after"][seat], top_other) - r["spent_before"][seat])
        spend = K_SPEND * danger(r["greens"], r["left"]) * (used - growth)
        top_spender = -K_TOP * danger(r["greens"], r["left"]) * lead(r["spent_after"])
        points = K_POINTS * (r["points_after"][seat] - r["points_before"][seat])
        if r["disgrace"]:      # took it: everyone else's forfeit counts; else: forfeits, if I raised too
            involved = seat in r["bidders"] or r["recipient"] == seat
            made_pay = K_MONEY * sum(v for s, v in r["paid"].items() if s != seat) if involved else 0.0
        else:                  # normal auction: the winner had to beat MY bid -- that much I made them pay
            made_pay = K_MONEY * sum(min(v, r["bids"].get(seat, 0)) for s, v in r["paid"].items() if s != seat)
        out.append((spend, top_spender, points, made_pay))
    return np.array(out, dtype=float).reshape(-1, len(TERMS))


def waste_penalty(record, seat):
    """End-of-game charge for money held beyond what staying above the poorest other player needed."""
    money = {s["seat"]: s["money_left"] for s in record["standings"]}
    poorest_other = min(v for s, v in money.items() if s != seat)
    return -K_WASTE * max(0, money[seat] - poorest_other - 1)


def decision_returns(record, seat, ledger=None):
    """Return of each of `seat`'s decisions (lines up with seats[seat].decisions), plus a breakdown dict."""
    ledger = ledger or rs.auction_ledger(record)
    per_auction = auction_rewards(ledger, seat)
    rank, waste = rank_reward(record, seat), waste_penalty(record, seat)
    final = rank + waste
    G, acc = np.zeros(len(per_auction)), final
    for k in reversed(range(len(per_auction))):
        acc = per_auction[k].sum() + GAMMA * acc
        G[k] = acc
    parts = dict(zip(TERMS, per_auction.sum(0)), rank=rank, waste=waste)
    return G[ledger["decision_rounds"][seat]], parts


def reward(record, seat):
    """Total reward of a whole game (all auctions + place), for a quick look."""
    _, parts = decision_returns(record, seat)
    return sum(parts.values())
