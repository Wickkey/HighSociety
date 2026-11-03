"""
Export a trained checkpoint for the game's RL bot (highsociety/code/ai/rl/).

    python export_weights.py runs/sp6/best.pt --name magnus-v1 --activate
    python export_weights.py runs/sp6/milestones/iter_000400.pt --name magnus-v2 --note "sp6 iter 400"

Writes highsociety/code/ai/rl/weights/<name>.npz (the network's weights, numpy)
and <name>.json (manifest: shape, source checkpoint, its last progress check).
--activate also points weights/active.json's "magnus" at it, i.e. the lobby's
Magnus bots play it once deployed. Then: python check_rl_bot.py --weights <name>,
commit, push.

Accepts a bare state_dict (best.pt, milestones/*.pt, imitation.pt) or a full
training checkpoint (latest.pt, which keeps the weights under "net").
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
import torch

from rl_model import ScoreNet

REPO = Path(__file__).resolve().parents[1]
WEIGHTS_DIR = REPO / "highsociety" / "code" / "ai" / "rl" / "weights"


def last_check(ckpt: Path, it):
    """The progress check (metrics.jsonl "eval" row) for this checkpoint's run and iteration, if any."""
    run = ckpt.parent.parent if ckpt.parent.name == "milestones" else ckpt.parent
    metrics = run / "metrics.jsonl"
    if not metrics.exists():
        return None
    evals = [json.loads(line) for line in metrics.open() if '"kind": "eval"' in line]
    if it is not None:
        evals = [e for e in evals if e["it"] == it]
    elif ckpt.name == "best.pt":          # best.pt = the first check with the highest vs_heuristics win rate
        best = None
        for e in evals:
            if best is None or e["vs_heuristics"]["win_rate"] > best["vs_heuristics"]["win_rate"]:
                best = e
        return best
    return evals[-1] if evals else None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("checkpoint")
    p.add_argument("--name", required=True, help="e.g. magnus-v2 (letters, digits, - and _)")
    p.add_argument("--activate", action="store_true", help='make the "magnus" difficulty play it')
    p.add_argument("--note", default="", help="free text kept in the manifest")
    p.add_argument("--force", action="store_true", help="overwrite an existing weights file of this name")
    a = p.parse_args()

    ckpt = Path(a.checkpoint)
    if not all(c.isalnum() or c in "-_" for c in a.name):
        sys.exit(f"bad --name {a.name!r}")
    npz, manifest_path = WEIGHTS_DIR / f"{a.name}.npz", WEIGHTS_DIR / f"{a.name}.json"
    if npz.exists() and not a.force:
        sys.exit(f"{npz} exists -- pick a new --name (or --force)")

    st = torch.load(ckpt, map_location="cpu", weights_only=False)
    state_dict = st["net"] if isinstance(st, dict) and "net" in st else st
    net = ScoreNet()
    net.load_state_dict(state_dict)                       # fails loudly on a shape mismatch
    layer = net.attention.layers[0]

    it = int(ckpt.stem.split("_")[-1]) if ckpt.stem.startswith("iter_") else None
    check = last_check(ckpt, it)
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(npz, **{k: v.detach().numpy().astype(np.float32) for k, v in net.state_dict().items()})
    manifest = {
        "name": a.name,
        "n_heads": layer.self_attn.num_heads,
        "n_layers": len(net.attention.layers),
        "d_model": net.card_id.shape[1],
        "params": sum(q.numel() for q in net.parameters()),
        "source": str(ckpt.resolve().relative_to(REPO)) if ckpt.resolve().is_relative_to(REPO) else str(ckpt),
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "progress_check": check,
        "note": a.note,
    }
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"wrote {npz.relative_to(REPO)} ({npz.stat().st_size / 1e6:.1f} MB) and {manifest_path.name}")

    if a.activate:
        active_path = WEIGHTS_DIR / "active.json"
        active = json.loads(active_path.read_text()) if active_path.exists() else {}
        active["magnus"] = a.name
        active_path.write_text(json.dumps(active, indent=1) + "\n")
        print(f'active.json: "magnus" -> {a.name}')
    print(f"next: python check_rl_bot.py --weights {a.name}   (then commit + push)")


if __name__ == "__main__":
    main()
