"""
The RL policy network, run with numpy only.

Training uses PyTorch (.RL/v2/model_v2.py's PolicyNet), but PyTorch is far too big a dependency for
the web server, and the network is small (~500k numbers), so its forward pass is re-implemented here
and fed the trained weights exported to an .npz file (.RL/v2/export_v2.py). .RL/v2/check_v2_server.py
checks this matches the PyTorch network's outputs.

PolicyNet: (K, 27, 6) boards -> (K, 1024) logits, one per move in candidates.SUBSETS order (index 0 =
pass), from a single pass over the current board. Illegal moves are masked by the caller (decision.py).
Each of the 27 rows is a token:
    1. embed      Linear(6 -> d) per row, plus a learned per-row vector (which card it is)
    2. attention  n_layers pre-norm transformer encoder layers (self-attention + ReLU MLP)
    3. head       flatten 27 x d -> Linear -> ReLU (body.1) -> Linear -> 1024 logits (policy)
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from highsociety.code.ai.rl.encoding import N_COLS, N_ROWS

WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"
_LN_EPS = 1e-5                     # torch.nn.LayerNorm's default


def _layer_norm(x, weight, bias):
    mean = x.mean(-1, keepdims=True)
    var = x.var(-1, keepdims=True)
    return (x - mean) / np.sqrt(var + _LN_EPS) * weight + bias


def _softmax(x, axis=-1):
    x = x - x.max(axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis, keepdims=True)


class PolicyNet:
    """Holds one set of trained weights; logits() is a pure function of its input."""

    def __init__(self, weights: dict, n_heads: int, n_layers: int):
        self.w = {k: np.asarray(v, dtype=np.float32) for k, v in weights.items()}
        self.n_heads, self.n_layers = n_heads, n_layers
        self.d_model = self.w["card_id"].shape[1]

    def logits(self, boards: np.ndarray) -> np.ndarray:
        """(K, 27, 6) boards -> (K, 1024) float32 logits (unmasked)."""
        w, d, H = self.w, self.d_model, self.n_heads
        x = boards.reshape(-1, N_ROWS, N_COLS).astype(np.float32)
        h = x @ w["embed.weight"].T + w["embed.bias"] + w["card_id"]            # (K, 27, d)
        K, T = h.shape[0], N_ROWS
        for i in range(self.n_layers):
            p = f"attention.layers.{i}."
            y = _layer_norm(h, w[p + "norm1.weight"], w[p + "norm1.bias"])
            qkv = y @ w[p + "self_attn.in_proj_weight"].T + w[p + "self_attn.in_proj_bias"]
            q, k, v = (a.reshape(K, T, H, d // H).transpose(0, 2, 1, 3) for a in np.split(qkv, 3, axis=-1))
            att = _softmax(q @ k.transpose(0, 1, 3, 2) / np.sqrt(d // H))       # (K, H, 27, 27)
            o = (att @ v).transpose(0, 2, 1, 3).reshape(K, T, d)
            h = h + o @ w[p + "self_attn.out_proj.weight"].T + w[p + "self_attn.out_proj.bias"]
            y = _layer_norm(h, w[p + "norm2.weight"], w[p + "norm2.bias"])
            f = np.maximum(y @ w[p + "linear1.weight"].T + w[p + "linear1.bias"], 0)
            h = h + f @ w[p + "linear2.weight"].T + w[p + "linear2.bias"]
        z = np.maximum(h.reshape(K, T * d) @ w["body.1.weight"].T + w["body.1.bias"], 0)
        return z @ w["policy.weight"].T + w["policy.bias"]


def load_weights(name: str) -> PolicyNet:
    """weights/<name>.npz plus its manifest weights/<name>.json."""
    manifest = json.loads((WEIGHTS_DIR / f"{name}.json").read_text())
    with np.load(WEIGHTS_DIR / f"{name}.npz") as f:
        weights = dict(f)
    return PolicyNet(weights, n_heads=manifest["n_heads"], n_layers=manifest["n_layers"])


@lru_cache(maxsize=None)
def load_network(name: str) -> PolicyNet:
    """One shared, read-only copy per weights file, however many bots use it."""
    return load_weights(name)
