# sp5 supervisor notes

## 2026-11-02 11:34 — start
- Same as sp4 (league of 4 spending checkpoints at p 0.5, bots in 30% of games, init sp2 iter 100, 1000 iterations), but place reward: 1st +2, 2nd 0, rest spread down to -1 (3p +2 0 -1 | 4p +2 0 -.5 -1 | 5p +2 0 -.33 -.67 -1).
- Check 0: vs_heuristics 17%, eliminated 38%, money left $20 (poorest bot $18).

## 2026-11-02 12:03 — stopped at iteration 93 (by request: spend-term change)
- Healthy through 75: wins vs bots 17 → 22 → 22 → 20%, money left $20 → 28 (poorest bot ~$17), eliminated 38 → 30%, vs_start 29/33/27%.
- Continued as runs/sp6 from milestone 50 with the two-sided spend term.
