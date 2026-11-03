"""
The RL scoring network, run with numpy only.

Training uses PyTorch (.RL/rl_model.py's ScoreNet), but PyTorch is far too big
a dependency for the web server, and the network is small (~250k numbers), so
its forward pass is re-implemented here and fed the trained weights exported
to an .npz file (.RL/export_weights.py). .RL/check_rl_bot.py checks this
matches the PyTorch model's scores.

Two architectures; each weights file's manifest says which ("arch", default "v1"):

v1 ScoreNet: (..., 27, 6) board -> (...) score, run on the board after each legal move.
v2 PolicyNet: (..., 27, 6) board -> (..., 1024) logits, one per move (candidates.SUBSETS order), run
    once per decision on the current board; illegal moves are masked before the softmax.

Both share the same body. Each of the 27 rows is a token:
    1. embed     Linear(6 -> d) per row, plus a learned per-row vector (which card it is)
    2. attention n_layers pre-norm transformer encoder layers (self-attention + ReLU MLP)
then v1: flatten 27 x d -> Linear -> ReLU -> Linear -> 1 score
     v2: flatten 27 x d -> Linear -> ReLU (body.1) -> Linear -> 1024 logits (policy)
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


class _Body:
    """The shared part: embedding + attention layers. Subclasses add their head."""
    arch = None

    def __init__(self, weights: dict, n_heads: int, n_layers: int):
        self.w = {k: np.asarray(v, dtype=np.float32) for k, v in weights.items()}
        self.n_heads, self.n_layers = n_heads, n_layers
        self.d_model = self.w["card_id"].shape[1]

    def _tokens(self, boards: np.ndarray) -> np.ndarray:
        """(K, 27, 6) boards -> (K, 27, d) attended tokens."""
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
        return h


class ScoreNet(_Body):
    """v1: one score per board (the board after a candidate move)."""
    arch = "v1"

    def score(self, boards: np.ndarray) -> np.ndarray:
        """(K, 27, 6) boards -> (K,) float32 scores."""
        w = self.w
        h = self._tokens(boards)
        z = np.maximum(h.reshape(len(h), -1) @ w["head.1.weight"].T + w["head.1.bias"], 0)
        return (z @ w["head.3.weight"].T + w["head.3.bias"]).reshape(len(h))


class PolicyNet(_Body):
    """v2: 1024 logits per board, one per move in candidates.SUBSETS order (index 0 = pass)."""
    arch = "v2"

    def logits(self, boards: np.ndarray) -> np.ndarray:
        """(K, 27, 6) boards -> (K, 1024) float32 logits (unmasked)."""
        w = self.w
        h = self._tokens(boards)
        z = np.maximum(h.reshape(len(h), -1) @ w["body.1.weight"].T + w["body.1.bias"], 0)
        return z @ w["policy.weight"].T + w["policy.bias"]


_ARCHS = {"v1": ScoreNet, "v2": PolicyNet}


def load_weights(name: str):
    """weights/<name>.npz plus its manifest weights/<name>.json -> a ScoreNet or PolicyNet."""
    manifest = json.loads((WEIGHTS_DIR / f"{name}.json").read_text())
    with np.load(WEIGHTS_DIR / f"{name}.npz") as f:
        weights = dict(f)
    cls = _ARCHS[manifest.get("arch", "v1")]
    return cls(weights, n_heads=manifest["n_heads"], n_layers=manifest["n_layers"])


@lru_cache(maxsize=None)
def load_network(name: str):
    """One shared, read-only copy per weights file, however many bots use it."""
    return load_weights(name)
