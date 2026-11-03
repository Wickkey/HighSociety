"""Where does the model lose? Plays fresh games vs bots and breaks the results down."""
"""
Where does a model lose? Plays N fresh games (greedy) vs capped / capped+greedy bots on fixed deals
(seeds 80,000,000+, the same 150 deals used for every run since sp6) and breaks the results down.

    nice -n 15 python v2/analyze.py <weights.pt> 150        (from .RL; v1 ScoreNet or v2 PolicyNet weights)
"""
import os, sys, json, collections
from pathlib import Path
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np, torch
import rl_seat as rs
import train_v2
torch.set_num_threads(1)

ckpt, N = sys.argv[1], int(sys.argv[2])
OPP = sys.argv[3] if len(sys.argv) > 3 else "bots"     # "bots" (capped / capped+greedy) or an engine bot, e.g. "medium"
net = train_v2.load_any(ckpt)
MyPolicy = lambda net, greedy=True: train_v2.make_policy(net, greedy)

games = []
for i in range(N):
    n = [3, 4, 5][i % 3]; seat = i % n; r = np.random.default_rng(5000 + i)
    opp = ["capped"] * (n - 1) if r.random() < 0.5 else \
        [str(o) for o in r.permutation(["capped", "greedy"] + list(r.choice(["capped", "greedy"], n - 3)))]
    if OPP != "bots":
        opp = [OPP] * (n - 1)
    rec, rl = rs.play_game(MyPolicy(net, greedy=True), opponents=opp, seat=seat, seed=80_000_000 + i)
    led = rs.auction_ledger(rec)
    kinds = {s: ("RL" if s == seat else opp[s if s < seat else s - 1]) for s in range(n)}
    games.append((n, seat, kinds, rec, led))
json.dump([], open("/dev/null", "w"))

def pct(x): return f"{100*x:.0f}%"
# ---- outcomes
rows = []
for n, seat, kinds, rec, led in games:
    stn = {s["seat"]: s for s in rec["standings"]}
    me = stn[seat]
    others = [stn[s] for s in stn if s != seat]
    alive_others = [o for o in others if not o["eliminated"]]
    best_other_pts = max([o["points"] for o in alive_others], default=-99)
    rows.append(dict(n=n, win=me["is_winner"], elim=me["eliminated"], pts=me["points"], money=me["money_left"],
                     poorest_other=min(o["money_left"] for o in others),
                     second_poorest=sorted(s["money_left"] for s in stn.values())[1],
                     best_other_pts=best_other_pts, any_other_elim=any(o["eliminated"] for o in others)))
W = sum(r["win"] for r in rows); E = sum(r["elim"] for r in rows)
place_count = collections.Counter()
for (n, seat, kinds, rec, led) in games:
    key = {s["seat"]: (s["eliminated"], -s["points"]) for s in rec["standings"]}
    place_count["eliminated" if key[seat][0] else sorted(key, key=key.get).index(seat) + 1] += 1
print(f"== {N} games, model {ckpt}, opponents: {OPP}")
print(f"wins {pct(W/N)} (chance {pct(np.mean([1/r['n'] for r in rows]))}) | eliminated {pct(E/N)} | "
      f"lost while not eliminated {pct((N-W-E)/N)}")
print("places: " + ", ".join(f"{k}{'' if k == 'eliminated' else 'st nd rd th th'.split()[k-1]}: {v/N:.0%}"
                             for k, v in sorted(place_count.items(), key=lambda kv: str(kv[0]))))
for n in (3,4,5):
    rr=[r for r in rows if r["n"]==n]
    print(f"  {n}p: win {pct(np.mean([r['win'] for r in rr]))} elim {pct(np.mean([r['elim'] for r in rr]))}")

el = [r for r in rows if r["elim"]]
if el:
    gaps = [r["second_poorest"] - r["money"] for r in el]
    print(f"\n-- eliminated games ({len(el)}): short of 2nd-poorest by median ${np.median(gaps):.0f}; "
          f"<= $5 short in {pct(np.mean([g <= 5 for g in gaps]))}; points they'd have had: median {np.median([r['pts'] for r in el]):.1f} "
          f"vs best other {np.median([r['best_other_pts'] for r in el]):.1f}")
lost = [r for r in rows if not r["win"] and not r["elim"]]
if lost:
    print(f"-- lost, not eliminated ({len(lost)}): points behind winner median {np.median([r['best_other_pts']-r['pts'] for r in lost]):.1f}; "
          f"money left above poorest median ${np.median([r['money']-r['poorest_other'] for r in lost]):.0f} "
          f"(>= $20 unused in {pct(np.mean([r['money']-r['poorest_other']>=20 for r in lost]))})")
wn = [r for r in rows if r["win"]]
if wn:
    print(f"-- wins ({len(wn)}): money above poorest median ${np.median([r['money']-r['poorest_other'] for r in wn]):.0f}, points {np.median([r['pts'] for r in wn]):.1f}")

# ---- per-auction behaviour: RL vs bots
agg = collections.defaultdict(lambda: collections.defaultdict(list))
for n, seat, kinds, rec, led in games:
    for rd in led["rounds"]:
        t = rd["card"]["type"]; v = rd["card"]["value"]
        late = rd["greens"] >= 3
        for s, kind in kinds.items():
            a = agg[kind]
            took = rd["recipient"] == s
            if t == "Painting":
                a["paint_seen"].append(1)
                if took:
                    a["paint_won"].append(v); a["paint_price"].append(rd["paid"].get(s, 0)); a["price_per_pt"].append(rd["paid"].get(s, 0)/v)
                    if late: a["late_price_per_pt"].append(rd["paid"].get(s, 0)/v)
            elif t == "PrestigeCard":
                a["prestige_seen"].append(1)
                if took: a["prestige_won"].append(1); a["prestige_price"].append(rd["paid"].get(s, 0))
            else:
                a["disgrace_seen"].append(1)
                if took: a["disgrace_taken"].append(t)
                a["disgrace_forfeit"].append(rd["paid"].get(s, 0))
            a["never_bid"].append(s not in rd["bidders"] and not rd["disgrace"])
            if late: a["late_spend"].append(rd["paid"].get(s, 0))
print("\n-- per seat type: share of cards taken, prices, disgrace")
print(f"{'':7s}{'paint share':>12s}{'$/pt':>7s}{'late $/pt':>10s}{'prestige share':>15s}{'$/prestige':>11s}{'stuck w/ disgrace':>18s}{'$ lost dodging':>15s}{'never bid':>10s}")
for kind in ("RL", "capped", "greedy", "medium"):
    a = agg[kind]
    if not a: continue
    m = lambda k: np.mean(a[k]) if a[k] else 0
    print(f"{kind:7s}{len(a['paint_won'])/len(a['paint_seen']):12.2f}{m('price_per_pt'):7.2f}{m('late_price_per_pt'):10.2f}"
          f"{len(a['prestige_won'])/max(1,len(a['prestige_seen'])):15.2f}{m('prestige_price'):11.1f}"
          f"{len(a['disgrace_taken'])/max(1,len(a['disgrace_seen'])):18.2f}{m('disgrace_forfeit'):15.1f}{m('never_bid'):10.0%}")
print("  share = fraction of those cards a seat of this type ended up with (1/n is an even split)")

# ---- eliminated games: where did the money go, and when did it become the poorest?
print("\n-- eliminated games: spending and timing")
would_win, spend_mix, poorest_from, last_round_spend = 0, collections.Counter(), [], []
for (n, seat, kinds, rec, led), r in zip(games, rows):
    if not r["elim"]:
        continue
    stn = {s["seat"]: s for s in rec["standings"]}
    alive_best = max(s["points"] for s in stn.values() if s["seat"] != seat and not s["eliminated"])
    would_win += r["pts"] > alive_best
    for rd in led["rounds"]:
        p = rd["paid"].get(seat, 0)
        if p:
            t = rd["card"]["type"]
            spend_mix["disgrace (dodging)" if rd["disgrace"] else t] += p
    # first round after which it stayed the (strictly or tied) poorest = most spent
    k_from = None
    for k, rd in enumerate(led["rounds"]):
        sa = rd["spent_after"]
        if sa[seat] >= max(sa.values()):
            k_from = k if k_from is None else k_from
        else:
            k_from = None
    poorest_from.append(len(led["rounds"]) - k_from if k_from is not None else 0)
    last = led["rounds"][-1]
    last_round_spend.append(last["paid"].get(seat, 0))
tot = sum(spend_mix.values())
print(f"  would have WON on points if not eliminated: {pct(would_win/len(el))} of eliminated games")
print("  where its money went: " + ", ".join(f"{k} {pct(v/tot)}" for k, v in spend_mix.most_common()))
print(f"  was already the top spender for the last {np.median(poorest_from):.0f} auctions (median) -- "
      f"top spender during the final 3+ auctions in {pct(np.mean([x >= 3 for x in poorest_from]))} of them")

# ---- end game: money held in the last auctions, and what it let go
print("\n-- end game (auctions after the 3rd green card is out, i.e. the next green ends the game)")
held, passed_cheap, n_late, lost_rows = [], [], 0, []
for (n, seat, kinds, rec, led), r in zip(games, rows):
    late = [rd for rd in led["rounds"] if rd["greens"] >= 3 and not rd["disgrace"]]
    for rd in late:
        n_late += 1
        money_mine = 106 - rd["spent_before"][seat]
        poorest_other = min(106 - v for s, v in rd["spent_before"].items() if s != seat)
        held.append(money_mine - poorest_other)
        if rd["card"]["type"] == "Painting" and rd["recipient"] not in (None, seat):
            price = rd["paid"].get(rd["recipient"], 0)
            # could it have outbid and still stayed above the poorest other player?
            if money_mine - (price + 1) > poorest_other:
                passed_cheap.append((rd["card"]["value"], price, seat in rd["bidders"], r["win"]))
    if not r["win"] and not r["elim"]:
        lost_rows.append(r)
print(f"  late normal auctions seen: {n_late}; its money above the poorest other at those moments: median ${np.median(held):.0f}")
if passed_cheap:
    pc = np.array([(v, p, b, w) for v, p, b, w in passed_cheap], float)
    print(f"  late paintings it let others take though it could outbid and stay above the poorest: {len(pc)} "
          f"({len(pc)/len(games):.1f} per game); value median {np.median(pc[:,0]):.0f}, price median ${np.median(pc[:,1]):.0f}; "
          f"it never bid on {pct(np.mean(pc[:,2] == 0))} of them; {pct(np.mean(pc[:,3] == 0))} were in games it lost")

# ---- eliminated-but-would-have-won games: how much extra did it spend, and which purchase cost the game?
def score(cards):
    p = sum(v for t, v in cards if t == "Painting") - 5 * sum(t == "Passe" for t, v in cards)
    p *= 2 ** sum(t == "PrestigeCard" for t, v in cards)
    return p * (0.5 if any(t == "Scandale" for t, v in cards) else 1)

print("\n-- eliminated games it would have WON on points: the overspend")
cases = []
for (n, seat, kinds, rec, led), r in zip(games, rows):
    if not r["elim"]:
        continue
    stn = {s["seat"]: s for s in rec["standings"]}
    best_alive = max(s["points"] for s in stn.values() if s["seat"] != seat and not s["eliminated"])
    if r["pts"] <= best_alive:
        continue
    money = {s: st["money_left"] for s, st in stn.items()}
    others_sorted = sorted(v for s, v in money.items() if s != seat)
    gap = others_sorted[0] - money[seat]          # next poorer player's money minus mine; tying (gap 0) eliminates nobody
    mine = [(rd["card"]["type"], rd["card"]["value"], rd["paid"].get(seat, 0), rd) for rd in led["rounds"] if rd["recipient"] == seat]
    cards = [(t, v) for t, v, p, rd in mine]
    dodge = sum(rd["paid"].get(seat, 0) for rd in led["rounds"] if rd["disgrace"] and rd["recipient"] != seat)
    # skip one purchase: money back, card gone; it survives if it is no longer the unique poorest
    flips = []
    for k, (t, v, p, rd) in enumerate(mine):
        if p <= 0 or rd["disgrace"]:
            continue
        m2 = money[seat] + p
        rest = cards[:k] + cards[k + 1:]
        if m2 >= others_sorted[0] and score(rest) > best_alive:
            flips.append((p, t, v))
    cases.append(dict(gap=gap, pts=r["pts"], best=best_alive, flips=flips, dodge=dodge, n=n))
if cases:
    g = np.array([c["gap"] for c in cases])
    print(f"  {len(cases)} games ({pct(len(cases)/len(games))} of all games). Spent more than the next poorer player by:")
    print(f"    median ${np.median(g):.0f} | $1-5: {pct(np.mean(g <= 5))} | $6-15: {pct(np.mean((g > 5) & (g <= 15)))} | "
          f"$16-30: {pct(np.mean((g > 15) & (g <= 30)))} | over $30: {pct(np.mean(g > 30))}")
    print(f"  its points lead over the best surviving player: median {np.median([c['pts'] - c['best'] for c in cases]):.1f}")
    f = [c for c in cases if c["flips"]]
    print(f"  skipping ONE purchase would have both kept it in AND still won: {len(f)} of {len(cases)} games")
    if f:
        cheapest = [min(c["flips"]) for c in f]
        print("    cheapest such purchase each time: " + ", ".join(f"{t[:9]} {v} for ${p}" for p, t, v in sorted(cheapest)))
    print(f"  money lost dodging disgrace cards in these games: median ${np.median([c['dodge'] for c in cases]):.0f} "
          f"(dodging alone would have covered the gap in {pct(np.mean([c['dodge'] >= c['gap'] for c in cases]))})")
    print("  per game (gap $, points lead, players): " + "; ".join(f"${c['gap']} +{c['pts']-c['best']:.0f}pts {c['n']}p" for c in sorted(cases, key=lambda c: c['gap'])))
