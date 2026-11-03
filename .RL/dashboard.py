"""
Writes <run>/dashboard.html: a self-contained page (no server, no internet needed) showing how a
training run is going. rl_train.py rewrites it after every iteration and progress check; the page
reloads itself every 30 s. Open it once and leave it open:

    open runs/sp1/dashboard.html

If it stops being updated (crash, Mac asleep, process killed) the page turns its status banner red
after STALE_MINUTES, because it compares the time it was written with the browser's clock.

Can also be run by hand:  python dashboard.py runs/sp1
"""
import html
import json
import sys
import time
from pathlib import Path

STALE_MINUTES = 10


def _read_jsonl(path):
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass            # a line being written right now
    return rows


def write_dashboard(run):
    run = Path(run)
    rows = _read_jsonl(run / "metrics.jsonl")
    config = json.loads((run / "config.json").read_text()) if (run / "config.json").exists() else {}
    log_tail = []
    if (run / "train.log").exists():
        log_tail = (run / "train.log").read_text().splitlines()[-25:]
    data = {"rows": rows, "config": config, "written": time.time(), "run": str(run),
            "stale_minutes": STALE_MINUTES}
    page = TEMPLATE.replace("__DATA__", json.dumps(data)).replace(
        "__LOG__", html.escape("\n".join(log_tail)))
    tmp = run / "dashboard.html.tmp"
    tmp.write_text(page)
    tmp.replace(run / "dashboard.html")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RL Training Run</title>
<style>
:root { --bg:#f7f7f5; --card:#fff; --ink:#1d1d1b; --mute:#6b6b66; --line:#e3e3de;
        --a:#2f6fdf; --b:#d9822b; --c:#2e9e6a; --d:#a24fd1; --bad:#c8372d; --ok:#2e9e6a; --warn:#c98a00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161615; --card:#1f1f1d; --ink:#ecebe6; --mute:#9a9992; --line:#33332f;
          --a:#6b9bff; --b:#f0a050; --c:#4cc38a; --d:#c58af0; --bad:#ff6b5e; --ok:#4cc38a; --warn:#e6b23a; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system, system-ui, sans-serif; }
main { max-width:1200px; margin:0 auto; padding:20px 16px 40px; }
h1 { font-size:20px; margin:0 0 4px; } .sub { color:var(--mute); margin-bottom:16px; }
.banner { padding:12px 16px; border-radius:10px; font-weight:600; margin-bottom:16px; color:#fff; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px,1fr)); gap:12px; margin-bottom:16px; }
.kpi, .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.kpi .v { font-size:22px; font-weight:650; } .kpi .l { color:var(--mute); font-size:12px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(340px,1fr)); gap:12px; }
.card h2 { font-size:14px; margin:0 0 2px; } .card p { margin:0 0 8px; color:var(--mute); font-size:12px; }
svg { width:100%; height:200px; display:block; }
.legend { display:flex; flex-wrap:wrap; gap:10px; font-size:12px; color:var(--mute); margin-top:4px; }
.legend i { display:inline-block; width:10px; height:3px; margin-right:4px; vertical-align:middle; }
pre { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; overflow-x:auto;
      font-size:12px; white-space:pre-wrap; word-break:break-word; margin:0; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th, td { text-align:right; padding:4px 6px; border-bottom:1px solid var(--line); } th:first-child, td:first-child { text-align:left; }
</style></head>
<body><main>
<h1>RL training run</h1>
<div class="sub" id="sub"></div>
<div class="banner" id="banner"></div>
<div class="kpis" id="kpis"></div>
<div class="grid" id="charts"></div>
<div class="card" style="margin-top:12px"><h2>Progress checks</h2><p>Greedy play, same deck seeds every check. Win rate vs chance (1/players).</p><div id="checks"></div></div>
<h2 style="font-size:14px;margin:16px 0 6px">Last lines of train.log</h2>
<pre>__LOG__</pre>
</main>
<script>
const D = __DATA__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const rows = D.rows, iters = rows.filter(r => r.kind === "iter"), evals = rows.filter(r => r.kind === "eval"),
      status = rows.filter(r => r.kind === "status");
const last = iters[iters.length - 1], lastEval = evals[evals.length - 1], lastStatus = status[status.length - 1];
const total = D.config.iters || null;
const pct = v => v == null ? "–" : (100 * v).toFixed(0) + "%";
const ago = s => s < 90 ? Math.round(s) + " s ago" : s < 5400 ? Math.round(s / 60) + " min ago" : (s / 3600).toFixed(1) + " h ago";

document.getElementById("sub").textContent = D.run + " · page written " + new Date(D.written * 1000).toLocaleString() + " · reloads every 30 s";

// ---- status banner
const age = Date.now() / 1000 - D.written, b = document.getElementById("banner");
let msg, color;
if (lastStatus && lastStatus.event === "finish") { msg = "Finished: " + (last ? last.it : 0) + " iterations."; color = css("--ok"); }
else if (lastStatus && lastStatus.event === "crash") { msg = "Crashed at iteration " + lastStatus.it + ": " + (lastStatus.error || "") + ". The launcher retries in 60 s; see the log below."; color = css("--bad"); }
else if (lastStatus && lastStatus.event === "early_stop") { msg = "Stopped automatically at iteration " + lastStatus.it + ": win rate vs heuristics stayed below half of chance. Best weights are in best.pt."; color = css("--warn"); }
else if (lastStatus && lastStatus.event === "stopped") { msg = "Stopped by hand at iteration " + lastStatus.it + ". Resume with --resume."; color = css("--warn"); }
else if (age > D.stale_minutes * 60) { msg = "No update for " + ago(age).replace(" ago", "") + ". The run may have stopped: crashed, Mac asleep, or killed. Check the terminal / log."; color = css("--bad"); }
else { msg = "Running. Last update " + ago(age) + "."; color = css("--ok"); }
b.textContent = msg; b.style.background = color;

// ---- KPIs
const recent = iters.slice(-20), secPer = recent.length ? recent.reduce((s, r) => s + r.seconds, 0) / recent.length : null;
const evalSec = evals.length ? evals.reduce((s, r) => s + (r.seconds || 0), 0) / evals.length : 0;
const everyEval = D.config.eval_every || 25;
const perIt = secPer == null ? null : secPer + evalSec / everyEval;
const eta = (perIt && total && last) ? (total - last.it) * perIt : null;
const kpi = (l, v) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`;
document.getElementById("kpis").innerHTML =
  kpi("iteration", last ? last.it + (total ? " / " + total : "") : "0") +
  kpi("games played", last ? last.games.toLocaleString() : "0") +
  kpi("per iteration", perIt ? perIt.toFixed(1) + " s" : "–") +
  kpi("time left (est.)", eta ? (eta / 3600).toFixed(1) + " h" : "–") +
  kpi("vs start (latest)", lastEval && lastEval.vs_start ? pct(lastEval.vs_start.win_rate) + " <small style='font-size:12px;color:var(--mute)'>chance " + pct(lastEval.vs_start.chance) + "</small>" : "–") +
  kpi("vs heuristics (latest)", lastEval ? pct(lastEval.vs_heuristics.win_rate) + " <small style='font-size:12px;color:var(--mute)'>chance " + pct(lastEval.vs_heuristics.chance) + "</small>" : "–");

// ---- charts (inline SVG)
function smooth(xs, w) { return xs.map((_, i) => { const s = xs.slice(Math.max(0, i - w + 1), i + 1).filter(v => v != null); return s.length ? s.reduce((a, b) => a + b, 0) / s.length : null; }); }
function chart(title, note, x, series, opts = {}) {
  const W = 560, H = 200, L = 40, R = 10, T = 10, B = 22;
  const all = series.flatMap(s => s.y).filter(v => v != null && isFinite(v));
  if (!x.length || !all.length) return `<div class="card"><h2>${title}</h2><p>${note}</p><p>No data yet.</p></div>`;
  let lo = opts.min ?? Math.min(...all), hi = opts.max ?? Math.max(...all);
  if (hi === lo) { hi += 1; lo -= 1; }
  const x0 = Math.min(...x), x1 = Math.max(...x) === x0 ? x0 + 1 : Math.max(...x);
  const sx = v => L + (v - x0) / (x1 - x0) * (W - L - R), sy = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let g = "";
  for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4, y = sy(v);
    g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="${css("--line")}"/><text x="${L - 4}" y="${y + 4}" font-size="10" text-anchor="end" fill="${css("--mute")}">${opts.pct ? (100 * v).toFixed(0) + "%" : +v.toFixed(2)}</text>`; }
  g += `<text x="${W - R}" y="${H - 4}" font-size="10" text-anchor="end" fill="${css("--mute")}">iteration ${x1}</text><text x="${L}" y="${H - 4}" font-size="10" fill="${css("--mute")}">${x0}</text>`;
  for (const s of series) {
    let d = "", pen = false;
    s.y.forEach((v, i) => { if (v == null || !isFinite(v)) { pen = false; return; } d += (pen ? "L" : "M") + sx(x[i]).toFixed(1) + "," + sy(v).toFixed(1); pen = true; });
    g += `<path d="${d}" fill="none" stroke="${css(s.c)}" stroke-width="${s.w || 2}" ${s.dash ? 'stroke-dasharray="4 4"' : ""}/>`;
    if (s.dots) s.y.forEach((v, i) => { if (v != null) g += `<circle cx="${sx(x[i])}" cy="${sy(v)}" r="2.5" fill="${css(s.c)}"/>`; });
  }
  const leg = series.map(s => `<span><i style="background:${css(s.c)}"></i>${s.name}</span>`).join("");
  return `<div class="card"><h2>${title}</h2><p>${note}</p><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${g}</svg><div class="legend">${leg}</div></div>`;
}
const ex = evals.map(r => r.it), ix = iters.map(r => r.it), W = 15;
const get = (r, k) => (r[k] ? r[k].win_rate : null);
const charts = [
  chart("Is it improving?", "Win rate of the current network vs its own iteration-0 self and vs the previous check. Above the dashed chance line = better.",
    ex, [{ name: "vs start", y: evals.map(r => get(r, "vs_start")), c: "--a", dots: 1 },
         { name: "vs previous check", y: evals.map(r => get(r, "vs_prev")), c: "--d", dots: 1 },
         { name: "chance", y: evals.map(r => (r.vs_start || r.vs_heuristics).chance), c: "--mute", dash: 1, w: 1 }], { pct: 1, min: 0, max: 1 }),
  chart("Fixed yardstick: vs heuristic bots", "One seat vs engine bots (all capped, or capped + greedy). Also: how often it ends up eliminated (least money).",
    ex, [{ name: "win rate", y: evals.map(r => r.vs_heuristics.win_rate), c: "--c", dots: 1 },
         { name: "eliminated", y: evals.map(r => r.vs_heuristics.eliminated), c: "--bad", dots: 1 },
         { name: "chance", y: evals.map(r => r.vs_heuristics.chance), c: "--mute", dash: 1, w: 1 }], { pct: 1, min: 0, max: 1 }),
  chart("Money left at game end (vs bots)", "Its average money left out of $106, next to the poorest bot's. Far below the poorest bot = overspending, often eliminated; drifting toward $106 = hoarding / always passing.",
    ex, [{ name: "our model", y: evals.map(r => r.vs_heuristics.avg_money_left ?? null), c: "--b", dots: 1 },
         { name: "poorest bot", y: evals.map(r => r.vs_heuristics.poorest_other_money_left ?? null), c: "--mute", dots: 1 }], { min: 0 }),
  chart("Money left at game end (self-play training games)", "Learner seats, smoothed: their money left vs the poorest other player's, and how often they are eliminated (money in $ out of 106; eliminated shown as a % on the same scale).",
    ix, [{ name: "learner", y: smooth(iters.map(r => r.money_left ?? null), W), c: "--b" },
         { name: "poorest other", y: smooth(iters.map(r => r.poorest_other_money_left ?? null), W), c: "--mute" },
         { name: "eliminated x100", y: smooth(iters.map(r => r.eliminated == null ? null : 100 * r.eliminated), W), c: "--bad" }], { min: 0 }),
  chart("Self-play reward per game", "Learner seats, smoothed over " + W + " iterations. Flat is normal: its opponents improve too.",
    ix, ["3", "4", "5"].map((n, k) => ({ name: n + " players", y: smooth(iters.map(r => r.reward[n] ?? null), W), c: ["--a", "--b", "--c"][k] }))),
  chart("Reward terms per game", "Average per learner per game, smoothed. Place should dominate; watch for one per-auction term running away.",
    ix, [["rank", "--a"], ["spend", "--bad"], ["top_spender", "--d"], ["waste", "--warn"], ["points", "--c"], ["made_pay", "--b"]].map(([k, c]) => ({ name: k, y: smooth(iters.map(r => r.terms[k] ?? null), W), c }))),
  chart("Exploration", "Entropy of its bid choice (higher = more spread out) and the sampling temperature.",
    ix, [{ name: "entropy", y: smooth(iters.map(r => r.entropy), W), c: "--d" }, { name: "temperature", y: iters.map(r => r.temperature), c: "--mute", dash: 1 }]),
  chart("Cards per bid", "Average cards added per bid. The bot warm start is 1.0; multi-card bids have to be learned.",
    ix, [{ name: "cards per bid", y: smooth(iters.map(r => r.cards_per_bid), W), c: "--b" }]),
  chart("Seconds per iteration", "Training speed (progress checks not included).",
    ix, [{ name: "seconds", y: smooth(iters.map(r => r.seconds), W), c: "--mute" }]),
];
document.getElementById("charts").innerHTML = charts.join("");

// ---- checks table
const rowsHtml = evals.slice().reverse().map(r => `<tr><td>${r.it}</td><td>${r.games.toLocaleString()}</td><td>${pct(get(r, "vs_start"))}</td><td>${pct(get(r, "vs_prev"))}</td><td>${pct(r.vs_heuristics.win_rate)}</td><td>${pct(r.vs_heuristics.chance)}</td><td>${pct(r.vs_heuristics.eliminated)}</td><td>${r.vs_heuristics.avg_points}</td><td>${r.vs_heuristics.avg_money_left ?? ""}</td></tr>`).join("");
document.getElementById("checks").innerHTML = evals.length ?
  `<table><tr><th>iteration</th><th>games</th><th>vs start</th><th>vs prev</th><th>vs heuristics</th><th>chance</th><th>eliminated</th><th>avg points</th><th>money left</th></tr>${rowsHtml}</table>` : "<p>No checks yet.</p>";
</script>
</body></html>
"""


if __name__ == "__main__":
    write_dashboard(sys.argv[1])
    print(f"wrote {sys.argv[1]}/dashboard.html")
