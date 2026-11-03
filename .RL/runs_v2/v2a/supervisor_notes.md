# v2a supervisor notes

## 2026-11-03 12:48 — start
- v2 network (board read once, 1,024 move outputs + mask, critic head), 511k params, CPU.
- Init: runs_v2/distill_v2_critic_reset.pt = sp9 (iters 1000/1300) distilled into v2 (90% move agreement, KL 0.03;
  150 fresh deals: 49% wins, 26% eliminated -- same as sp9), critic output layer reset (it had memorised).
- Critic learns from iteration 0, drives advantages from 600. Exploration T 1.6 -> 1.0, entropy 0.03 -> 0.01 over 600.
- Table themes: mixed 55% (seat by seat; fixed set = 4 v1 spenders + sp9 iter 1300; 30% bot games: medium /
  medium+vcapped / vcapped / vcapped+greedy), all-sp9 10%, all-own-copies 10%, all-conservative 10% (vcapped 2.0-2.8,
  sp3 hoarder), all-aggressive 10% (vcapped 4.2-5.0, v1 spenders), all-MCTS-medium 5%. vcapped appetite 2-5.
- Rewards unchanged from sp9. Checks every 25 (+ 30 games vs MCTS medium every 50). 2000 iterations.
- Check 0: vs bots 42%, vs MCTS medium 30% (chance 26%; first measurement ever vs medium), eliminated 27%.
- Speed: 7-15 s/iter (sp9 ~20 s). cards/bid 1.42-1.51 at T 1.6.

## 2026-11-03 13:24 — review 1 (iteration ~150)
- Checks it 25-125: vs bots 37-45% (best.pt 45% at 50), vs MCTS medium 37% at it 100 (30% at 0), eliminated
  27 -> 20 -> 8 -> 12%, money left $29 -> $34 -> $40 -> $41 vs poorest bot $13. vs_start 22-29%.
- Watch: money left climbing well above the poorest bot while eliminations fall -- leaning cautious. Wins vs bots and
  medium still fine, so not hoarding yet. Exploration still high (T 1.45).
- Critic R2 0.02 at it 150 (learning). ~7-23 s/iter. Action: none.

## 2026-11-03 13:35 — stopped at the iteration above (user decision)
- Fresh-deal places, iter 150 vs start: 1st 48% -> 38%, 2nd 18% -> 34%, eliminated 24% -> 23%: trading wins for a safe 2nd (worth 0).
- Continued as runs_v2/v2b from the same distilled start with 2nd place = -0.25.

## 2026-11-03 13:32 — resumed at 163 with the SAME settings (user decision)
- The v2b attempts (2nd = -0.25, then 1st = +3) were cancelled before training. Plan: keep 1st +2 / 2nd 0 and decide at
  iteration 1000 whether the drift into safe 2nd places reverses once exploration ends (600) and the critic is on.
- Track finishing places on fresh deals (v2/analyze.py now prints them). Start: 1st 48%, 2nd 18%, eliminated 24%;
  iter 150: 1st 38%, 2nd 34%, eliminated 23% (90 games).

## 2026-11-03 13:50 — review 2 (iteration ~243)
- Checks it 175/200/225: vs bots 47/32/47%, vs MCTS medium 40% (it 200), vs_start 31-33%, eliminated ~20%,
  money $36-40 vs poorest bot $12-14. T 1.36, cards/bid ~1.39, critic R2 0.03 (learning). ~8 s/iter. Action: none.

## 2026-11-03 13:58 — critic switched OFF (user decision), resumed at 268
- User: actor and critic should not share weights (different jobs; shared body lets them spoil each other's learning).
  v2a now: --value-weight 0 (no critic gradient into the shared body) and --critic-from 1e9 (never used); advantages =
  return - running average per table size, as in sp9. The value layer stays in the files, unused (lossless resume).
- Iterations 0-268 did train the shared body with the critic loss at weight 0.5 (fresh-deal check at 225 looked fine:
  1st 47%, 2nd 21%, eliminated 25%).
- Next run (later): actor and critic as SEPARATE networks.
- Decision at ~1000 (bigger win reward or not) still stands. The "critic R2" in the log is now just the unused head.

## 2026-11-03 14:24 — review 3 (iteration ~379)
- Checks it 300-375: vs bots 55% (best.pt at 300) / 52 / 47 / 50%; vs MCTS medium 50% at it 350 (30-40% before);
  vs_start 31-42% (above chance); eliminated 17-25%; money $32-38 vs poorest bot $12-14.
- Training: T 1.22, cards/bid ~1.37, ~7-9 s/iter. Improving. Action: none.
- Move stats (iter 350, 150 fresh deals, greedy): 86% one-card / 13.5% two-card bids; pairs mostly small change
  (1+4, 1+2, 2+3) or big card + small top-up (2+15, 2+25); $6/$10/$12 never paired; multi-card bids 24% of early-game
  bids, 2-3% mid/late.

## 2026-11-03 14:54 — review 4 (iteration 500)
- Checks it 400-500: vs bots 53-57% (best.pt 57% at 475), vs MCTS medium 40/47% (450/500), eliminated 15-20%,
  money $28-40 vs poorest bot $12-13, vs_start 18-40%. T 1.10, cards/bid 1.36, ~7 s/iter. Action: none.
- Next fresh-deal places analysis at ~600 (end of exploration).

## 2026-11-03 15:20 — review 5 (iteration ~609) — exploration ended at 600 (T 1.00)
- Checks it 550/575/600: vs bots 60% (best.pt) / 58 / 50%, vs MCTS medium 50% (550), 63% (600), eliminated 12-15%,
  money $36-38 vs poorest bot $11-12, cards/bid 1.31. ~5-7 s/iter.
- Started the iteration-600 benchmark (background, nice 15): 150 fresh games vs MCTS medium for v2a iter 600 and for
  the live Magnus (sp6 iter 100), 30 vs MCTS hard for v2a, then v2/analyze.py places vs bots.

## 2026-11-03 15:49 — review 6 (iteration ~705)
- Checks it 625-700: vs bots 47-57%, vs MCTS medium 43% (700), eliminated 23-27% (up from 12-15% at 575-600).
- BENCHMARK v2a iter 600 vs MCTS medium, 150 fresh games: 41% wins (chance 26%), eliminated 15%,
  places 1st 41 / 2nd 29 / 3rd 12 / 4th 3 / 5th 1 / eliminated 15%. (The 30-game checks said 50-63%: they flatter.)
- Live Magnus (sp6 iter 100) benchmark + hard check + places vs bots still running.

## 2026-11-03 16:00 — iteration-600 benchmark complete (fresh deals, seeds 85M+ / 80M+)
| model | vs MCTS medium (150) | vs MCTS hard (30) | vs capped/greedy (150) |
|---|---|---|---|
| v2a iter 600 | 41% wins, 15% elim (1st 41/2nd 29/3rd 12/4th 3/5th 1/elim 15) | 67% wins, 13% elim | 47% wins, 23% elim (1st 46/2nd 23/3rd 6/4th 2/elim 23) |
| live Magnus (sp6 iter 100) | 13% wins, 32% elim | - | - |
Chance 26%. v2a beats Medium clearly and is ~3x the live Magnus vs Medium. Hard: 30 games only (+-17%), consistent with
"hard ~ medium". Candidate for Magnus v2 -- needs the v2 network in the server's numpy code (highsociety/code/ai/rl).

## 2026-11-03 16:19 — review 7 (iteration ~833)
- Checks it 725-825: vs bots 47-55%, vs MCTS medium 60% at 800 (30 games), eliminated 22-27% (up from 12-15% around
  600), money $29-32 vs poorest bot $12-14, cards/bid 1.24 (above 1.2). ~6 s/iter. Action: none. Decision point ~1000.

## 2026-11-03 16:49 — review 8 (iteration ~955)
- Checks it 850-950: vs bots 45-48%, vs MCTS medium 33% (900, 30 games), eliminated 25-35% (up from 12-15% at ~600),
  money $22-29 vs poorest bot $14-15. Eliminations creeping back up after exploration ended. Action: none.
- Shipped insane-v1 = iter 600 (PR #45). The iteration-1000 comparison is next.

## 2026-11-03 17:20 — ITERATION-1000 DECISION POINT (fresh deals)
| checkpoint | vs capped/greedy (150): 1st / 2nd / elim | vs MCTS medium (150): win / elim |
|---|---|---|
| start (distilled) | 48 / 17 / 26 | - |
| iter 225 | 47 / 21 / 25 | - |
| iter 600 | 46 / 23 / 23 | 41% / 15% |
| iter 1000 | 44 / 27 / 21 | 47% / 8% (places 1st 45 / 2nd 30 / 3rd 13 / 4th 3 / 5th 1 / elim 8) |
- vs Medium (the real benchmark) clearly improving 600 -> 1000; overpaying down ($3.64 -> $3.25 per painting point).
- vs the weak bots, a slow drift from 1st to 2nd (48 -> 44% 1st, 17 -> 27% 2nd) while eliminations fall.
- Recommendation to user: keep v2a's rewards; ship iter 1000 as insane-v2; next run (separate critic network)
  could test 1st = +2.5.

## 2026-11-03 17:49 — review 9 (iteration ~1195)
- Checks it 1100-1175: vs bots 47-57%, vs MCTS medium 50% (1150), vs_start 36-47% (clearly above chance),
  eliminated 17-22%, money $34-38 vs poorest bot $13-14. Entropy 0.95, cards/bid 1.20. ~7-11 s/iter. Action: none.

## 2026-11-03 18:19 — review 10 (iteration ~1316)
- Checks it 1200-1300: vs bots 37-55%, vs MCTS medium 40/47% (1250/1300), vs_start 38-47%, eliminated 15-23%,
  money $32-39 vs poorest bot $12-14. Entropy 0.95, cards/bid 1.23. ~7-13 s/iter. Action: none.

## 2026-11-03 18:49 — review 11 (iteration ~1430)
- Checks it 1375-1425: vs bots 45-55%, vs MCTS medium 43% (1400), vs_start 42-53%, eliminated 20-33%. Action: none.
- Shortcomings, iter 1300 vs bots (150 fresh deals): 51% wins (1st 49 / 2nd 17 / elim 29), $3.62/painting pt
  (capped $2.76); eliminations it would have won on points: 68% of its eliminations, median $25 overspent, 12-point lead;
  late cheap paintings let go 0.6/game (price now median $12).
- Odd-move scan vs MCTS medium (30 games, iter 1300, v2/odd_moves.py): escalating bidding wars on low paintings
  (e.g. $26 for Painting 1), big jumps (e.g. $15 to dodge Passe when $3 would do), occasional extra card, rare
  expensive disgrace dodges while far ahead.

## 2026-11-03 19:19 — review 12
- Pass analysis (iter 1300, 60 games vs medium + 60 vs bots): passes ~1/3 of decisions; more selective early, fights
  mid-game; drops out of paintings at ~$4-4.6/pt vs medium, ~$2.5-3/pt vs bots (adapts to table). Weak spot: in
  disgrace auctions when ahead 8+ and money tight it takes the card only ~29-43% of the time (keeps paying to dodge).
- Training-game mix confirmed from metrics (29k games): mixed 55%, all-own-copies 10%, sp9 10%, conservative 10%,
  aggressive 10%, medium 5%.
- CHECK it 1475: vs_heuristics 47% (chance 26%) | vs_start 44% (chance 26%) | vs_prev 20% (chance 26%) | eliminated 12% | money left $42 (poorest bot $15) | 6.7s
- CHECK it 1500: vs_heuristics 53% (chance 26%) | vs_start 53% (chance 26%) | vs_prev 36% (chance 26%) | vs_medium 50% (chance 26%) | eliminated 22% | money left $34 (poo
- CHECK it 1525: vs_heuristics 58% (chance 26%) | vs_start 47% (chance 26%) | vs_prev 31% (chance 26%) | eliminated 22% | money left $34 (poorest bot $15) | 7.1s

## 2026-11-03 19:49 — review 13
- CHECK it 1600: vs_heuristics 57% (chance 26%) | vs_start 31% (chance 26%) | vs_prev 27% (chance 26%) | vs_medium 33% (chance 26%) | eliminated 28% | money left $27 (poo
- CHECK it 1625: vs_heuristics 55% (chance 26%) | vs_start 40% (chance 26%) | vs_prev 29% (chance 26%) | eliminated 28% | money left $30 (poorest bot $15) | 7.2s
- CHECK it 1650: vs_heuristics 65% (chance 26%) | vs_start 27% (chance 26%) | vs_prev 42% (chance 26%) | vs_medium 43% (chance 26%) | eliminated 23% | money left $31 (poo
- Action: none.

## 2026-11-03 20:19 — review 14
- CHECK it 1725: vs_heuristics 55% (chance 26%) | vs_start 42% (chance 26%) | vs_prev 33% (chance 26%) | eliminated 25% | money left $31 (poorest bot $12) | 7.0s
- CHECK it 1750: vs_heuristics 43% (chance 26%) | vs_start 40% (chance 26%) | vs_prev 22% (chance 26%) | vs_medium 50% (chance 26%) | eliminated 40% | money left $27 (poo
- CHECK it 1775: vs_heuristics 58% (chance 26%) | vs_start 44% (chance 26%) | vs_prev 31% (chance 26%) | eliminated 17% | money left $37 (poorest bot $11) | 7.3s
- Action: none.

## 2026-11-03 20:55 — iteration ~1930: checks 1875-1925 vs bots 50-62%, vs medium 50% (1900), eliminated 18-25%. Still running. bench_medium.py got a seed0 argument for the confirmation set.

## 2026-11-03 21:20 — SHIPPED (user's choice: decide from the logged metrics, no extra benchmarking)
- Run finished at 2000. Final check (it 2000) was the weakest late one: vs bots 45%, vs_start 31%, vs medium 33%.
- insane-v2 = iteration 1900 (strongest late check: vs bots 62%, vs_start 47%, vs medium 50%, eliminated 18%).
  Match test OK (1,213 decisions), 758 tests pass. Commit 6a8a9c3 on rl-bot; PR #45 description updated; not merged.
