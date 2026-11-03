# sp3 supervisor notes

## 2026-11-02 10:40 — start
- Reward: WIN_BONUS +1 (1st = +2; places otherwise +1 ... -1). 1000 iterations, temperature 1.0, entropy 0.01.
- Started from runs/sp2/milestones/iter_000100.pt. First try from sp2's iteration 143 (runs/sp3_bad_start) was dropped:
  those weights had drifted into hoarding (vs bots: $67 left vs poorest bot $12, 0% eliminated, 3% wins).
- sp2 swing, 30 greedy games vs bots: iter 75 $7 left / 70% elim / 13% win; iter 100 $20 / 63% / 17%;
  iter 125 $46 / 23% / 7%; iter 143 $61 / 13% / 3%. Iter 100 = best.
- Check 0: vs_heuristics 17%, eliminated 38%, money left $20 (poorest bot $18).
- If it swings again: lower --lr (1e-4 now) is the first thing to try.

## 2026-11-02 11:09 — review 1: AUTO-STOPPED at iteration 75 (left stopped)
- Checks vs bots: 17% → 12 → 7 → 7% wins; eliminated 38 → 22 → 7 → 5%; money left $20 → 44 → 58 → 60 (poorest bot ~$13).
- vs_prev 24% → 38% while vs bots fell: it keeps beating its own past copies while getting worse vs bots.
- Self-play (iter lines): learner money $16 → $48, poorest other $7 → $27, cards/bid 1.4 → 1.11, rank reward +0.54 at it 25 then falling.
- Behaviour (30 games vs bots, iter 75): $55 left vs poorest other $21, pass rate 71%, 13% wins.
- Diagnosis: hoarding again, with the win bonus. The whole self-play population hoards together (poorest other's money
  climbs too). Likely cause: when everyone at the table hoards, whoever spends becomes the poorest and is eliminated
  (-1), so spending is punished inside self-play -- a stable "everyone hoards" equilibrium. Bots (who do spend) are only
  in 20% of games, not enough to pull it out. Lowering lr would only slow the drift, so no auto-fix applied.
- Action: none (left stopped). Options for the user: permanent spending opponents in the pool (league of past
  checkpoints e.g. sp1 iter 300 / sp2 iter 75 / imitation), more bot games, or a relative end-money reward term.
