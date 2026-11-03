# sp4 supervisor notes

## 2026-11-02 11:17 — start
- Fix for sp2/sp3 hoarding (the self-play population hoarded together, so whoever spent became the poorest and was eliminated):
  - --league: 4 fixed opponents that spend money, never replaced (runs/league/: imitation, sp1 iter 300, sp2 iter 75, sp2 iter 100).
    Each non-learner seat is a league member with chance 0.5, else a recent snapshot.
  - --p-heuristic-game 0.3 (was 0.2): bots in 30% of games (capped, 2x capped, or capped + greedy).
- Reward unchanged from sp3: WIN_BONUS +1 (1st = +2). Started from sp2 iter 100; 1000 iterations, T 1.0, entropy 0.01.
- Check 0: vs_heuristics 17%, eliminated 38%, money left $20 (poorest bot $18).

## 2026-11-02 11:34 — stopped at iteration 50 (by request: reward change)
- Check 25: vs_heuristics 10%, eliminated 20%, money left $48 vs poorest bot $15 -- sliding toward hoarding even with the league.
- Continued as runs/sp5 with places 1st +2, 2nd 0, rest down to -1.
