# sp7 supervisor notes

## 2026-11-02 21:46 — start
- From sp6 iter 600; same setup as sp6 plus the top_spender term: -K_TOP (0.01) x danger x lead, every auction it
  ends as the top spender. Goal: fewer eliminations.
- Baseline (sp6 iter 600, 150 fresh games): 41% wins, 37% eliminated (54% of those would have won on points, median
  $22 short, top spender for the last ~7 auctions), $3.66/painting point, $7.9 lost per disgrace auction.
- Check 0: vs bots 45%, eliminated 33%, money $23 vs poorest bot $15. top_spender term ~ -0.02 to -0.08 per game.

## 2026-11-02 22:10 — review 1 (iteration ~55)
- Checks: it 25 vs bots 43%, eliminated 30%; it 50 vs bots 48% (best.pt), eliminated 27% (down from 33%).
  Money left $28-31 vs poorest bot $14 -- spending less, as intended; watch it doesn't go further.
- vs_start 18-20% (below chance 26%): it loses to its own start (sp6 iter 600) in self-play while doing better vs bots.
  Early; watch.
- MEMORY: process footprint 3.4 GB (iter 5) -> 5.7 GB (iter 55); the growth is CPU heap (MALLOC_TINY/SMALL 4.2 GB),
  not the GPU cache (gpu_mb ~380). So the empty_cache fix did not address it; sp6's speed recovered because of the
  restart. The engine alone doesn't leak (200 games flat). Tracing the training step to find it.

## 2026-11-02 22:25 — memory leak: periodic restart
- Trace: Python-side memory flat; growth (~100-150 MB/iter) is native, inside PyTorch 2.2.1's Mac-GPU backend --
  not freeable from the script. empty_cache() only handled the GPU allocator cache.
- Fix: rl_train.py --restart-every 50 (default): saves latest.pt and exits with code 3; run_training.sh resumes at once
  (lossless, not counted as a crash). Tested end to end. sp7 stopped at 66 and resumed with it.
- Longer-term option: upgrade PyTorch in the ptorch env (newer versions fixed several MPS leaks).

## 2026-11-02 22:42 — review 2 (iteration ~105)
- Checks: it 75 vs bots 47%, it 100 47%; eliminated 27 → 25% (sp6 baseline ~33-37%); money $34 vs poorest bot $14-15.
- vs_start 24% → 16%: below chance vs its own start (sp6 iter 600) in head-to-head. Better vs bots and fewer
  eliminations, but it now loses to a table of sp6 copies -- likely too cautious against aggressive spenders
  (more money left, fewer points). Watch: if vs bots also starts slipping, the top_spender charge is too strong.
- First planned restart at 100 worked (resumed ~4 s later); 22-26 s/iter; footprint 4.9 GB. Action: none.

## 2026-11-02 23:12 — review 3 (iteration ~176)
- Checks: it 150 vs bots 50% (best.pt), vs_start 22%, eliminated 28%; it 175 45%, vs_start 29%, eliminated 32%;
  money $29 vs poorest bot $15. vs bots ~45-50%; eliminations ~25-32% (sp6 ~33-37% on the same deals).
- vs_start recovering to chance (29%). ~19 s/iter, footprint 6.1 GB (restart due at 200). Action: none.
- Next ~2-hourly fresh-deal analysis (mistakes.py, 150 games) due around iteration 200-225.

## 2026-11-02 23:45 — review 4 (iteration ~249) + fresh-deal analysis of iter 225 (same 150 deals as sp6 baseline)
- Checks: it 200 47% (eliminated 23%), it 225 47% (28%); money $32-35 vs poorest bot $14-15; vs_start 20-22%.
- Fresh deals, sp6 iter 600 -> sp7 iter 225:
    wins 41% -> 41% | eliminated 37% -> 24% | lost while not eliminated 21% -> 35%
    top spender for last ~7 -> ~4 auctions before elimination; $ lost per disgrace auction 7.9 -> 6.5; $/painting pt 3.66 -> 3.61
    lost-not-eliminated games: >= $20 unused in 54% (was 47%), median 7 points behind the winner.
- Reading: the top_spender charge did its job on eliminations (-13 points) but the games it saved turned into points
  losses with money left over -- win rate unchanged. Still overpays per painting point vs the capped bot.
- ~18 s/iter, footprint 6.9 GB before the restart at 250. Action: none.

## 2026-11-03 00:12 — review 5 (iteration ~319)
- Check it 300: vs bots 55% (new best.pt, highest of any run), vs_start 31% (back above chance), eliminated 25%,
  money $29 vs poorest bot $14. ~24 s/iter, footprint 5.7 GB. Action: none.

## 2026-11-03 00:42 — review 6 (iteration ~389)
- Checks: it 350 53%, it 375 52% vs bots; eliminated 23%; money $30 vs poorest bot $13-14; vs_start 22-27%.
- ~22 s/iter, footprint 5.1 GB. Action: none.

## 2026-11-03 00:50 — stopped by request (fan noise) at the iteration above; resumable with ./run_training.sh runs/sp7 + original args
