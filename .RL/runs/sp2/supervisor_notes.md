# sp2 supervisor notes

## 2026-11-02 10:20 — review 1 (iteration ~102)
- Alive, ~15–21 s/iter. Checks: vs_heuristics 33% → 20 / 17 / 27 / 17% (chance 26%); vs_start 33 / 22 / 18 / 40%; vs_prev 16 / 16 / 40%; eliminated 43 → 38%.
- cards/bid 1.8 → ~1.37, entropy ~1.6–1.75 (not collapsing).
- Behaviour, 30 greedy games vs capped / capped+greedy (other seeds than the checks):
  - iter 0:   money left $4 (poorest other $29), eliminated 70%, win 3%, pass rate 30%
  - iter 100: money left $20 (poorest other $27), eliminated 63%, win 17%, pass rate 49%
- Reading: the iter-300 sp1 weights overspend and get eliminated a lot vs capped; sp2 is learning to keep more money, which is the right direction, not hoarding (still ~$20 left, poorest-other $27). The vs_heuristics dip is within noise (60 games, ±6%).
- Action: none. Watch: eliminated should keep falling; if money left climbs toward the others' and cards/bid → ~1.1 with wins falling, that's sp1's hoarding again.

## 2026-11-02 10:40 — sp2 stopped at iteration 143 (by request)
- Last stats: money left ~$34 (poorest other ~$14), eliminated ~17% in self-play. Continued as runs/sp3 from these weights (runs/sp2/net_iter_143.pt) with WIN_BONUS +1 (1st = +2), 1000 iterations. Supervision moves to runs/sp3/supervisor_notes.md.
