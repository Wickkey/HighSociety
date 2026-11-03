# sp6 supervisor notes

## 2026-11-02 12:03 — start
- New two-sided spend term: K_SPEND x danger x (used - growth). Money spent while staying at/below the top other spender is rewarded; a growing lead as top spender is penalised. (Before: penalty only.)
- Otherwise as sp5: league of 4 at p 0.5, bots in 30% of games, 1st +2 / 2nd 0 / rest down to -1, 1000 iterations.
- Started from sp5 iteration 50 (wins 22%, vs_start 33%, vs_prev 38%).
- Check 0: vs_heuristics 22%, eliminated 35%, money left $23 (poorest bot $18). Iter 1 spend term +0.144/game (was about -0.05).
- Watch for overspending now, as well as hoarding.

## 2026-11-02 12:35 — greedy evaluation fixed; checks before ~55 used old argmax rule
- Details: checks 0/25/50 showed wins 22 → 18 → 12%, money left $23 → 37 → 52, eliminated 35 → 22 → 7% -- looked like hoarding.
  But argmax play passed in 68% of turns where the policy's own P(pass) averaged 29%: hundreds of bids split the "bid"
  probability, so pass had the top single score. Sampled play of the same iter-50 net: $21 left, 13% wins.
- Fix (rl_model.MyPolicy greedy): pass if P(pass) >= 0.5, else the best-scored bid. Re-measured iter 50 greedy: $21 left,
  13% wins (30 games). Training (sampled) is unaffected. Resumed at iteration 59 so checks use the fixed rule.
- All earlier runs' check numbers (and sp3's auto-stop, best.pt choices) were measured with the flawed argmax.

## 2026-11-02 12:41 — review 1 (iteration 100), first checks with the fixed greedy rule
- Checks: it 75 vs bots 35%, vs_start 51%, vs_prev 22%, eliminated 35%, money $18 (poorest bot $15);
  it 100 vs bots 37% (new best.pt), vs_start 49%, vs_prev 18%, eliminated 38%, money $17 (poorest bot $17).
- Well above chance (26%) vs bots and vs its start. No hoarding (money ≈ poorest bot's). vs_prev a bit below chance
  (45 games, noisy). Eliminated ~35-38% is the weak spot: it often ends as / tied with the poorest. Watch it.
- Action: none.

## 2026-11-02 13:15 — review 2 (iteration ~157)
- Checks vs bots (fixed greedy rule): it 125 32%, it 150 33% wins (chance 26%); vs_start 53/53%; eliminated 38 → 32%;
  money left $21-22 vs poorest bot $15-16. Steady, above chance, no hoarding in the checks.
- Self-play (sampled): money left drifting up a little ($25-29 → $29-35, poorest other ~$13), elim 6-28%, cards/bid ~1.06,
  entropy ~1.3-1.4 (stable).
- Seconds/iter 18-20 → 40-73 from iteration ~130. Mostly CPU contention: the Magnus-bot parity check and the
  pytest suite ran 12:50-13:10. Bigger hands from the slight money drift may add some. Re-check next review.
- Action: none.

## 2026-11-02 13:42 — review 3 (iteration ~205)
- Checks vs bots: it 175 45% (new best.pt), it 200 40% (chance 26%); vs_start 42/49%; eliminated 32 → 38%;
  money left $16 vs poorest bot $14-15 -- spends close to the poorest bot, no hoarding.
- Self-play: money $30-37 (poorest other ~$11-17), entropy ~1.3-1.4, cards/bid ~1.1. Stable.
- Seconds/iter back to ~20-23 by iteration 205 (the 40-70 s stretch was CPU contention from the Magnus-bot checks).
  ETA ~5 h for the remaining ~795 iterations.
- Action: none. Improving.

## 2026-11-02 14:12 — review 4 (iteration ~274)
- Checks vs bots: it 225 47% (new best.pt), it 250 37% (chance 26%); vs_start 51 → 60%; eliminated 37 → 32%.
- Money left in checks $21 → $26 (poorest bot $15-16); self-play $36 vs poorest other $10. Drifting up a little --
  watch for the hoarding pattern (money well above poorest + falling wins); not there yet, wins still well above chance.
- Seconds/iter ~20-34. Action: none.

## 2026-11-02 14:42 — review 5 (iteration ~321)
- Checks vs bots: it 225 47% (best) → 250 37% → 275 38% → 300 28% (chance 26%); vs_start keeps rising 60 → 62 → 64%;
  eliminated ~30-32%; money left $23-24 vs poorest bot $14-15 (flat).
- Behaviour, 30 other-seed games vs bots (passmass.py):
    iter 225 greedy: 23% wins, $20 left, P(pass) 42% | iter 300 greedy: 20% wins, $24 left, P(pass) 42%
  Essentially the same play: no rise in passing, money barely moved. The 47 → 28% check slide is mostly noise
  around ~30-35% (60 games, ±6%), with 225 likely a lucky high.
- Self-play money $39 vs poorest other $18, cards/bid 1.06, entropy ~1.3. Action: none; if the next 2 checks stay
  near/below 28% while vs_start also stops rising, consider stopping and keeping best.pt.

## 2026-11-02 15:12 — review 6 (iteration ~354)
- Checks vs bots: it 300 28%, 325 35%, 350 27% (chance 26%) -- flat around ~30%; vs_start 64 → 67 → 62% (plateauing);
  vs_prev 27-38%; eliminated 30-37%; money left $22-25 vs poorest bot $15-16. No hoarding/overspending.
- Slower: 40-68 s/iter (was ~20) with similar decision counts and nothing else heavy running. The Mac is swapping hard
  (8.2 of 9.2 GB swap used, 8 GB RAM) -- likely the cause. ETA at ~55 s/iter: ~10 h for the remaining ~645 iterations.
- Action: none. Plateau note: vs bots hasn't beaten best.pt (225, 47%) since; if still flat by ~500, stopping
  and keeping best.pt is reasonable.

## 2026-11-02 15:42 — review 7 (iteration ~383)
- Check it 375: vs bots 32%, vs_start 62%, vs_prev 31%, eliminated 35%, money $25 vs poorest bot $16. Flat ~30%.
- 63-73 s/iter; swap 8.5 of 9.2 GB used (memory pressure). Action: none.

## 2026-11-02 16:12 — review 8 (iteration ~406)
- Check it 400: vs bots 50% (new best.pt; chance 26%), vs_start 69%, eliminated 27% (lowest yet), money $28 vs poorest bot $14.
- Self-play money climbing ($32-34 → $45, poorest other $16-19) -- keep watching for passing-without-bidding; the
  vs-bots check shows no sign of it yet.
- ~90 s/iter; swap 8.8 of 9.2 GB used, only ~0.4 GB free -- risk of the system killing a process. The launcher would
  resume from latest.pt (saved every 25 iterations). Action: none.

## 2026-11-02 16:42 — review 9 (iteration ~427)
- Check it 425: vs bots 42%, vs_start 62%, eliminated 33%, money $29 vs poorest bot $17. Healthy, around the 40s now.
- 72-87 s/iter; swap grew to 10.2 GB total (9.2 used). Action: none.

## 2026-11-02 17:12 — review 10 (iteration ~448)
- No new check since it 425 (42%). Self-play money $36-40 vs poorest other $17-19. 80-97 s/iter, swap 9.6/10.2 GB.
- Action: none.

## 2026-11-02 17:42 — review 11 (iteration ~468)
- Check it 450: vs bots 40%, vs_start 69%, vs_prev 31%, eliminated 32%, money $26 vs poorest bot $16. Steady ~40%.
- 72-84 s/iter, swap 8.8/9.2 GB. Action: none.

## 2026-11-02 18:15 — review 12 (iteration ~487)
- Check it 475: vs bots 43%, vs_start 64%, vs_prev 40%, eliminated 33%, money $23 vs poorest bot $15.
- Behaviour on 30 OTHER-seed games (passmass.py), greedy: iter 225 23% / iter 300 20% / iter 475 20% wins, $20-24 left,
  P(pass) ~40-42%. No hoarding. But on these deals the win rate sits at ~20% (chance 26%) while the checks say 40-50%.
  The checks always use the same 60 deals and best.pt is picked on them, so they likely flatter the model; the true
  win rate vs these bots is probably between the two. Recommend a large fresh-deal evaluation (e.g. 300+ games, new
  seeds) before trusting best.pt or shipping new Magnus weights.
- 92-110 s/iter. Action: none.

## 2026-11-02 18:42 — review 13 (iteration ~505)
- Check it 500: vs bots 45%, vs_start 60%, vs_prev 33%, eliminated 25% (lowest yet), money $25 vs poorest bot $13.
- ~97 s/iter, swap 9.5/10.2 GB. ETA ~13 h for the remaining ~495 iterations at this speed. Action: none.

## 2026-11-02 19:12 — review 14 (iteration ~523)
- No new check since it 500 (45%). ~98-111 s/iter; swap grew to 11.3 GB total (10.1 used). Action: none.

## 2026-11-02 19:42 — review 15 (iteration ~539)
- Check it 525: vs bots 43%, vs_start 62%, vs_prev 20%, eliminated 35%, money $23 vs poorest bot $16. Flat.
- ~93-96 s/iter, swap 10.4/11.3 GB. Action: none.

## 2026-11-02 20:12 — review 16 (iteration ~555)
- Check it 550: vs bots 50% (ties best; best.pt stays at 400 since it needs strictly higher), vs_start 64%, eliminated 33%,
  money $23 vs poorest bot $16.
- 100-142 s/iter; swap 10.8/11.3 GB with ~0.4 GB free. ETA ~13+ h. Action: none.

## 2026-11-02 20:42 — review 17 (iteration ~570)
- No new check since it 550 (50%). 139-163 s/iter (memory pressure). ETA ~17 h for ~430 iterations. Action: none.

## 2026-11-02 21:12 — review 18 (iteration ~583)
- Check it 575: vs bots 45%, vs_start 64%, vs_prev 33%, eliminated 35%, money $21 vs poorest bot $16. Flat at a plateau
  (~40-50% on the check deals since iteration ~400).
- 95-116 s/iter. Action: none.

## 2026-11-02 21:23 — slowdown fixed (iteration 586)
- Cause of the 20 → 100-160 s/iter slowdown: the training process had an 18 GB memory footprint on the 8 GB Mac
  (the Mac GPU allocator caches every batch size and never frees it) -> constant swapping.
- Fix in rl_train.py: torch.mps.empty_cache() after every iteration and every progress check; iter metrics now
  include gpu_mb. Stopped (state saved) and resumed at iteration 586 -- same run, nothing lost.

## 2026-11-02 21:42 — review 19 (iteration ~629)
- Checks: it 600 45%, it 625 43% vs bots; vs_start 58-62%; eliminated 33-35%; money $18-23 vs poorest bot $15-16. Plateau.
- Speed fixed: ~21 s/iter, GPU memory 247 MB. ETA ~2.5 h for ~370 iterations.
- Mistake analysis (150 fresh games, iter 600, see chat): 41% wins; eliminated 37% (54% of those would have won on
  points; median $22 short; top spender for the last ~7 auctions); overpays vs capped ($3.66 vs $2.75 per painting point,
  $7.9 vs $4.3 lost per disgrace auction). Proposed fix for the next run: danger-scaled charge for staying top spender.
- Action: none.

## 2026-11-02 21:45 — stopped at iteration 635 (continued as sp7 with the top-spender charge)
- Plateau since ~400: 40-50% vs bots on the check deals; 41% wins / 37% eliminated on 150 fresh games (iter 600).
- Resumable: ./run_training.sh runs/sp6 with the original args.
