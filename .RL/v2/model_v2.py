"""
v2 network: read the board ONCE, output one logit per possible move, mask the illegal ones.

v1 (rl_model.ScoreNet) scores every legal move by running the whole network on the board as it
would look after that move -- up to ~1,000 runs per decision. v2 runs once per decision:

    board (27, 6) -> embed + attention (same body as v1) -> flatten -> MLP -> 1024 logits
                                                                       \\-> value (optional critic)

Output i = the move SUBSETS[i] (rl_seat.py): the set of money cards it ADDS this turn; i = 0 is pass.
At most 5 cards on the table, so 1,024 = every subset of the 11 money cards with <= 5 cards.
Illegal moves (cards not in hand, too many cards, doesn't beat the high bid) are masked to -inf
before the softmax, so they get probability 0.

Greedy play uses the same two-step rule as v1 (MyPolicy): pass if P(pass) >= 0.5, else the
best legal bid -- so evaluations are comparable.
"""
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # .RL (rl_seat, rl_model ...)
import rl_seat as rs                                              # noqa: E402

N_MOVES = len(rs.SUBSETS)                                         # 1024
PASS = rs.PASS_CAND                                               # 0
DEVICE = torch.device("cpu")      # one board per decision: the CPU is fast enough, and avoids the MPS memory leak


class PolicyNet(nn.Module):
    def __init__(self, d_model=32, n_heads=4, n_layers=2, hidden=256, value_head=False):
        super().__init__()
        self.embed = nn.Linear(6, d_model)
        self.card_id = nn.Parameter(torch.randn(27, d_model) * 0.02)
        layer = nn.TransformerEncoderLayer(d_model, n_heads, dim_feedforward=4 * d_model,
                                           dropout=0.0, batch_first=True, norm_first=True)
        self.attention = nn.TransformerEncoder(layer, n_layers, enable_nested_tensor=False)
        self.body = nn.Sequential(nn.Flatten(), nn.Linear(27 * d_model, hidden), nn.ReLU())
        self.policy = nn.Linear(hidden, N_MOVES)
        self.value = nn.Linear(hidden, 1) if value_head else None

    def forward(self, x):
        """(B, 27, 6) boards -> (B, 1024) logits, and (B,) values if the critic head exists (else None)."""
        h = self.attention(self.embed(x) + self.card_id)
        z = self.body(h)
        return self.policy(z), (self.value(z).squeeze(-1) if self.value is not None else None)


def legal_mask(states):
    """(B, 27, 6) numpy boards -> (B, 1024) bool: which moves are legal."""
    m = np.zeros((len(states), N_MOVES), dtype=bool)
    for i, s in enumerate(states):
        m[i, rs.legal_bids(s)] = True
    return m


def masked_logits(net, states, mask, temperature=1.0):
    """Logits / temperature with illegal moves at -inf (and the values, if any)."""
    x = torch.as_tensor(np.asarray(states), dtype=torch.float32, device=DEVICE)
    logits, value = net(x)
    logits = (logits / temperature).masked_fill(~torch.as_tensor(mask, device=DEVICE), float("-inf"))
    return logits, value


class PolicyV2:
    """What the engine seat calls (same interface as rl_model.MyPolicy): state + mask -> 12-entry vector."""

    def __init__(self, model, greedy=True, temperature=1.0, seed=0):
        self.model, self.greedy, self.temperature = model, greedy, temperature
        self.rng = np.random.default_rng(seed)

    def choose(self, state):
        cands = rs.legal_bids(state)
        if len(cands) == 1:
            return PASS
        mask = np.zeros((1, N_MOVES), dtype=bool)
        mask[0, cands] = True
        with torch.no_grad():
            logits, _ = masked_logits(self.model, state[None], mask, self.temperature)
            probs = F.softmax(logits[0], dim=0).numpy().astype(float)
        if self.greedy:           # pass-or-bid by total probability, then the best bid (see module docstring)
            if probs[PASS] >= 0.5:
                return PASS
            bids = cands[cands != PASS]
            return int(bids[np.argmax(probs[bids])])
        return int(self.rng.choice(N_MOVES, p=probs / probs.sum()))

    def bid(self, state, mask):
        return rs.candidate_to_vector(self.choose(state))


def load(path, value_head=None):
    """A PolicyNet from a saved state_dict (or a training checkpoint's "net"); value head inferred if not given."""
    st = torch.load(path, map_location=DEVICE, weights_only=False)
    st = st["net"] if isinstance(st, dict) and "net" in st else st
    net = PolicyNet(value_head=("value.weight" in st) if value_head is None else value_head)
    net.load_state_dict(st, strict=value_head is None)
    return net.eval()
