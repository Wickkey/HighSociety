# sp9 supervisor notes

## 2026-11-03 02:32 — start (user asleep; 2000 iterations)
- From sp8 iter 100 (checks: 38% vs bots, eliminated 30%, money $31 vs poorest bot $16, vs_start 18%).
- Same reward/opponents as sp8 (incl. waste charge) + exploration: temperature 1.6 -> 1.0 and entropy bonus 0.03 -> 0.01
  over the first 200 iterations. Goal: learn multi-card bids (sp7 iter 225 added 1 card in 562 of 564 bids) and fix
  late-game passing; expect a dip in checks during exploration.
- Iter 1-2: T 1.60, cards/bid 1.26-1.27, ~25 s/iter.
- Note: the first launch (rejected in chat, --iters 1000) did start and was stopped after iteration 1 (state saved); the
  2000-iteration launcher resumed from it at 02:33. Single trainer running, target 2000 (config.json still says 1000 -- stale).

## 2026-11-03 02:50 — review 1 (iteration ~49)
- Check it 25: vs bots 48%, vs_start 29%, eliminated 22%, money $31 vs poorest bot $14. No exploration dip.
- T 1.46, entropy 1.57, cards/bid ~1.24 (training, sampled). ~20 s/iter. Action: none.

## 2026-11-03 — exploration extended: --explore-iters 200 -> 600 (user asked; T 1.6->1.0, entropy 0.03->0.01 now end at iteration 600). Lossless restart.

## 2026-11-03 03:18 — review 2 (iteration ~124)
- Checks: it 75 vs bots 43%, eliminated 22%, money $31; it 100 45%, vs_start 40%, eliminated 38% (one-check jump),
  money $23 vs poorest bot $17.
- Training: T 1.48, entropy 1.70, cards/bid 1.39 (rising from 1.27). ~21 s/iter, footprint 4.4 GB. Action: none.

## 2026-11-03 03:48 — review 3 (iteration ~206)
- Checks it 150/175/200: vs bots 43/43/42%, vs_start 22/33/29%, eliminated 33/27/32%, money $22-29 vs poorest bot $15-16.
  Steady ~42-45% during exploration; no collapse.
- Training: T 1.40, entropy ~1.72, cards/bid ~1.31-1.39. ~18 s/iter, footprint 3.3 GB. Action: none.

## 2026-11-03 04:18 — review 4 (iteration ~296)
- Checks it 225/250/275: vs bots 40/38/38% (slow slide from ~45%), vs_start 38/31/31% (above chance), eliminated
  27-32%, money $23-26 vs poorest bot $15. Mild dip during exploration (T still 1.30) -- expected; not acting.
- Training cards/bid rising: 1.42 (from 1.27). Entropy 1.55. ~19 s/iter, footprint 6.3 GB (restart due at 300).

## 2026-11-03 04:48 — review 5 (iteration ~378)
- Checks it 300-375: vs bots 38/43/40/38%, vs_start 27/29/18/38%, eliminated 23/17/25/22% (lower than before),
  money $27-33 vs poorest bot $13-14. Flat ~40% during exploration (T 1.22); eliminations improving.
- cards/bid ~1.38 in training. ~18 s/iter, footprint 5.9 GB. Action: none.

## 2026-11-03 05:30 — review 6 (iteration ~460) + fresh-deal analysis of iter 450 (same 150 deals)
- Checks it 400/425/450: vs bots 35/42/43%, eliminated 20/22/32%, money $24-35 vs poorest bot $14-16. T 1.14.
- Fresh deals, iter 450: 45% wins (best of any checkpoint so far; sp6-600 41%, sp7-225 41%, sp7-300 39%),
  eliminated 34%, lost on points 21%. Still: $3.55/painting pt (capped $2.76); eliminations it would have won 20%
  of games (median $27 overspent); $7.4 lost per disgrace auction; late cheap paintings let go 0.6/game.
- Action: none. Promising; exploration ends at 600.

## 2026-11-03 05:48 — review 7 (iteration ~535)
- Checks it 475/500/525: vs bots 40/37/40%, vs_start 31-33%, eliminated 25-30%, money $25-34 vs poorest bot $15.
- Training: T 1.07, entropy 1.40, cards/bid 1.19 -- falling back as temperature drops (1.42 at T 1.30). ~22 s/iter,
  footprint 6.3 GB. Action: none; the real test is cards/bid after iteration 600.

## 2026-11-03 06:18 — review 8 (iteration ~614) — exploration finished at 600 (T now 1.00)
- Checks it 550/575/600: vs bots 35/45/40%, vs_start 40/36/44% (clearly above chance -- better than its start),
  eliminated 28-32%, money $25-31 vs poorest bot $14-16.
- cards/bid at T 1.0: 1.17 (was ~1.06-1.12 at T 1.0 in sp6/sp7; peak 1.42 during exploration). Slightly higher than
  before; watch whether it holds above ~1.15. ~17 s/iter. Action: none.

## 2026-11-03 06:48 — review 9 (iteration ~693)
- Checks it 625/650/675: vs bots 40/47/38%, vs_start 36/29/33%, eliminated 28-32%, money $24-32 vs poorest bot $14-15.
- T 1.00, entropy 1.32, cards/bid 1.21 (holding above ~1.15 after exploration). ~20 s/iter, 5.1 GB. Action: none.

## 2026-11-03 07:30 — review 10 (iteration ~775) + fresh-deal analysis of iter 750
- Checks it 700/725/750: vs bots 47/47/45%, vs_start 29/29/27%, vs_prev 18/16/18% (below chance 3x -- each newer
  checkpoint loses to the one 25 iterations earlier head-to-head), eliminated 25-28%. Entropy 1.32 -> 1.20.
- Fresh deals, iter 750: 43% wins, 35% eliminated, 22% lost on points (iter 450: 45/34/21%) -- flat, not regressing.
  Same weaknesses: $3.66/painting pt, eliminations-it-would-have-won median $31 overspent, 0.6 cheap late paintings
  let go per game, $6.6 lost per disgrace auction.
- Reading: plateau around 43-45% on fresh deals; vs_prev < chance looks like self-play cycling, not a collapse.
  Action: none (the v2 architecture starts when this run ends).

## 2026-11-03 07:48 — review 11 (iteration ~847)
- Checks it 775/800/825: vs bots 43/43/42%, vs_start 29/44/40%, vs_prev 38/18/36%, eliminated 27-32%, money $27-30.
- cards/bid 1.17, entropy ~1.25. ~21 s/iter, footprint 6.6 GB (restart at 850). Plateau. Action: none.

## 2026-11-03 08:18 — review 12 (iteration ~922)
- Checks it 850/875/900: vs bots 43/43/45%, eliminated 25/25/20%, money $30-34 vs poorest bot $14-16;
  vs_start 31 -> 24 -> 20% and vs_prev 18/18/20% (below chance). Head-to-head vs its own earlier versions is
  sliding while vs bots holds; entropy 1.25 -> 1.17. Likely self-play specialising/cycling. Next fresh-deal analysis
  ~iteration 1000 decides whether it's a real regression. Action: none.

## 2026-11-03 09:00 — review 13 (iteration ~1000) + fresh-deal analysis of iter 1000
- Checks it 950/975/1000: vs bots 42% each, vs_start 31/33/22%, vs_prev 22/20/27%, eliminated 27-33%.
- Fresh deals, iter 1000: 49% wins (best yet: iter 450 45%, iter 750 43%, sp6-600 41%, sp7-225 41%), eliminated 28%,
  lost on points 23%. So the sub-chance vs_prev/vs_start is NOT a regression against the bots.
  Still: $3.62/painting pt; when eliminated, would have won on points 71% of the time; lost-on-points games end with
  median $37 unused; 0.6 cheap late paintings let go per game.
- Action: none. Candidate for Magnus v2 weights: iter 1000 (49% fresh deals) vs live magnus-v1 (sp6 ~iter 100).

## 2026-11-03 09:18 — review 14 (iteration ~1077)
- Checks it 1025/1050/1075: vs bots 40/35/37%, vs_start 44/36/40%, vs_prev 36/29/29%, eliminated 23-28%, money $32-34.
- cards/bid 1.26, entropy 1.26. Mild dip on the check deals; iter 1000 was 49% on fresh deals. Action: none.

## 2026-11-03 09:48 — review 15 (iteration ~1152)
- Checks it 1100/1125/1150: vs bots 42/40/42%, vs_start 42/38/40%, vs_prev 22/16/24%, eliminated 18-28%, money $30-34
  vs poorest bot $13-15. cards/bid 1.18. Steady. Action: none.

## 2026-11-03 10:18 — review 16 (iteration ~1232)
- Checks it 1175/1200/1225: vs bots 43/45/47%, vs_start 36/33/29%, eliminated 23-30%, money $26-32. cards/bid 1.27.
  Action: none. Next fresh-deal analysis ~iteration 1250-1300.

## 2026-11-03 11:00 — review 17 (iteration ~1310) + fresh-deal analysis of iter 1300
- Checks it 1250/1275/1300: vs bots 35/40/40%, vs_start 27-29%, eliminated 18-25%, money $29-38 vs poorest bot $12-13.
- Fresh deals, iter 1300: 48% wins, 31% eliminated, 21% lost on points (iter 1000: 49/28/23%). Plateau ~48-49%.
  Same weaknesses ($3.54/pt, 0.6 cheap late paintings let go/game, eliminations it would have won 61%).
- Action: none.

## 2026-11-03 11:18 — review 18
- CHECK it 1325: vs_heuristics 42% (chance 26%) | vs_start 18% (chance 26%) | vs_prev 42% (chance 26%) | eliminated 23% | money left $32 (poorest bot $14) | 62.3s
- CHECK it 1350: vs_heuristics 38% (chance 26%) | vs_start 22% (chance 26%) | vs_prev 24% (chance 26%) | eliminated 23% | money left $34 (poorest bot $13) | 62.8s
- CHECK it 1375: vs_heuristics 43% (chance 26%) | vs_start 29% (chance 26%) | vs_prev 20% (chance 26%) | eliminated 23% | money left $29 (poorest bot $12) | 61.6s
- Action: none.

## 2026-11-03 11:48 — review 19
- CHECK it 1400: vs_heuristics 37% (chance 26%) | vs_start 27% (chance 26%) | vs_prev 20% (chance 26%) | eliminated 22% | money left $33 (poorest bot $13) | 62.4s
- CHECK it 1425: vs_heuristics 42% (chance 26%) | vs_start 24% (chance 26%) | vs_prev 20% (chance 26%) | eliminated 23% | money left $31 (poorest bot $15) | 63.1s
- CHECK it 1450: vs_heuristics 47% (chance 26%) | vs_start 29% (chance 26%) | vs_prev 24% (chance 26%) | eliminated 27% | money left $29 (poorest bot $15) | 64.3s
- Plateau (fresh deals ~48-49% since iter 1000). Proposed to the user: stop early, pick weights, start v2. Action: none (awaiting answer).

## 2026-11-03 11:55 — stopped by the user at the iteration above (plateau). Resumable.
