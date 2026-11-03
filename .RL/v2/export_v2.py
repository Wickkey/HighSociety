"""
Export a v2 checkpoint (v2/model_v2.PolicyNet) for the game's RL bot (highsociety/code/ai/rl/).

    python v2/export_v2.py runs_v2/v2a/milestones/iter_000600.pt --name insane-v1 --activate --note "..."

Writes highsociety/code/ai/rl/weights/<name>.npz (policy weights only -- the value head, if any, is not
needed to play) and <name>.json (manifest with "arch": "v2", shape, source, last progress check).
--activate points weights/active.json's "insane" at it. Then: python v2/check_v2_server.py --weights <name>.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_v2 as m2                                                      # noqa: E402

RL_DIR = Path(__file__).resolve().parents[1]
REPO = RL_DIR.parent
WEIGHTS_DIR = REPO / "highsociety" / "code" / "ai" / "rl" / "weights"


def last_check(ckpt: Path):
    run = ckpt.parent.parent if ckpt.parent.name == "milestones" else ckpt.parent
    metrics = run / "metrics.jsonl"
    if not metrics.exists() or not ckpt.stem.startswith("iter_"):
        return None
    it = int(ckpt.stem.split("_")[-1])
    evals = [json.loads(l) for l in metrics.open() if '"kind": "eval"' in l]
    return next((e for e in evals if e["it"] == it), None)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("checkpoint")
    p.add_argument("--name", required=True)
    p.add_argument("--activate", action="store_true", help='make the "insane" difficulty play it')
    p.add_argument("--note", default="")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    if not all(c.isalnum() or c in "-_" for c in a.name):
        sys.exit(f"bad --name {a.name!r}")
    npz, man = WEIGHTS_DIR / f"{a.name}.npz", WEIGHTS_DIR / f"{a.name}.json"
    if npz.exists() and not a.force:
        sys.exit(f"{npz} exists -- pick a new --name (or --force)")

    ckpt = Path(a.checkpoint)
    net = m2.load(ckpt)                                                     # PolicyNet (value head if present)
    sd = {k: v.detach().numpy().astype(np.float32) for k, v in net.state_dict().items() if not k.startswith("value.")}
    np.savez(npz, **sd)
    layer = net.attention.layers[0]
    manifest = {
        "name": a.name, "arch": "v2",
        "n_heads": layer.self_attn.num_heads, "n_layers": len(net.attention.layers),
        "d_model": net.card_id.shape[1], "n_moves": int(sd["policy.weight"].shape[0]),
        "params": int(sum(v.size for v in sd.values())),
        "source": str(ckpt.resolve().relative_to(REPO)) if ckpt.resolve().is_relative_to(REPO) else str(ckpt),
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "progress_check": last_check(ckpt), "note": a.note,
    }
    man.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"wrote {npz.relative_to(REPO)} ({npz.stat().st_size / 1e6:.1f} MB) and {man.name}")
    if a.activate:
        active_path = WEIGHTS_DIR / "active.json"
        active = json.loads(active_path.read_text()) if active_path.exists() else {}
        active.pop("magnus", None)                                          # the difficulty's pre-launch name
        active["insane"] = a.name
        active_path.write_text(json.dumps(active, indent=1) + "\n")
        print(f'active.json: "insane" -> {a.name}')


if __name__ == "__main__":
    main()
