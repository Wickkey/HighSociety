# sp8 supervisor notes

## 2026-11-03 01:48 — start
- From sp7 iter 225 (150 fresh deals: 41% wins, 24% eliminated, 35% lost on points). Same as sp7 plus the end-of-game
  waste charge: -K_WASTE (0.005) x max(0, my money - poorest other's money - 1).
- Goal: use spare money (fewer points losses with $20+ unused, take cheap late paintings) without bringing
  eliminations back.
- sp7 iter 300 (best.pt) on the same deals: 39% wins, 33% eliminated; overpays $3.64/painting pt (capped $2.77);
  eliminations it would have won: median $26 overspent with a median 8.5-point lead.
- Check 0: vs bots 47%, eliminated 28%, money $32 vs poorest bot $15. Iter 1: waste -0.12, top_spender ~0.

## 2026-11-03 02:14 — review 1 (iteration ~64)
- Checks: it 25 vs bots 42%, eliminated 35%, money $26; it 50 47%, eliminated 33%, money $25 (poorest bot $15).
  Money came down ($32 -> $25) as intended, but eliminations rose (28% -> 33-35%). Exactly the trade-off risk; at the
  ~30-33% line. vs_start 29-31% (above chance). Self-play money $22 vs $13, waste ~-0.05/game.
- ~20 s/iter, footprint 4.1 GB. Action: none yet; if eliminated stays >= 33% over the next 2 checks with wins not
  rising, the waste charge is too strong.

## 2026-11-03 02:32 — stopped at 105; continued as sp9 (exploration) from iter 100. Check 100: 38% vs bots, eliminated 30%, money $31, vs_start 18% -- waste charge had not clearly helped yet.
