"""
The network and the policy that plays with it.

    ScoreNet      (..., 27, 6) board -> (...) score; attention over the 27 card rows
    score_boards  scores a numpy stack of boards on the model's device (padded to fixed sizes on the GPU)
    MyPolicy      what the engine calls: lists the legal bids, scores each one's afterstate, picks one

Shared by RL_exploration.ipynb and rl_train.py, so edits here apply to both
(the notebook picks them up via %autoreload).
"""
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

import rl_seat as rs

# Mac GPU if available (~3-4x faster for the attention net), else CPU
DEVICE = torch.device("mps" if torch.backends.mps.is_available() else "cpu")


class ScoreNet(nn.Module):
    """
    (..., 27, 6) afterstate -> (...) score: how good is it to be in this position?

    Reads the board like a sentence: each of the 27 rows (a money card, painting,
    prestige or disgrace card) is one TOKEN, and its 6 numbers (me, the 4 other
    seats, the game) are that token's raw features.
      1. embed     each token's 6 numbers -> a d_model vector, plus a learned
                   "which card is this" vector (like a word's position embedding --
                   without it, $25 and Painting 3 would look alike when their 6
                   numbers match)
      2. attention n_layers of self-attention: every token looks at every other
                   one, so e.g. "Painting 9 is on auction" can pull in "I still hold
                   the $25" and "seat 2 already owns two prestige cards"
      3. head      flatten the 27 updated tokens -> MLP -> one score
    """
    def __init__(self, d_model=32, n_heads=4, n_layers=2, hidden=256):
        super().__init__()
        self.embed = nn.Linear(6, d_model)
        self.card_id = nn.Parameter(torch.randn(27, d_model) * 0.02)
        layer = nn.TransformerEncoderLayer(d_model, n_heads, dim_feedforward=4 * d_model,
                                           dropout=0.0, batch_first=True, norm_first=True)
        self.attention = nn.TransformerEncoder(layer, n_layers, enable_nested_tensor=False)
        self.head = nn.Sequential(
            nn.Flatten(),                               # (B, 27, d_model) -> (B, 27 * d_model)
            nn.Linear(27 * d_model, hidden), nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, x):
        lead = x.shape[:-2]                             # any batch dims, e.g. (B,) or (B, K)
        h = self.embed(x.reshape(-1, 27, 6)) + self.card_id   # (B, 27, d_model) tokens
        h = self.attention(h)                           # (B, 27, d_model), each token now "aware" of the rest
        return self.head(h).reshape(lead)


def score_boards(model, boards):
    """
    Scores a (K, 27, 6) numpy stack of boards -> (K,) tensor on the model's own device (the Mac
    GPU in the training process, the CPU in the game-playing workers). On the GPU, K is padded up
    to a power of 2 (min 64): it re-prepares its kernels for every new batch shape, and the number
    of legal bids differs every turn, so without padding it's ~3x slower than the CPU. The CPU
    needs no padding.
    """
    device = next(model.parameters()).device
    x = torch.from_numpy(boards).float()
    K = len(boards)
    if device.type == "mps":
        P = max(64, 1 << (K - 1).bit_length())
        x = torch.cat([x, x.new_zeros(P - K, 27, 6)])
    return model(x.to(device))[:K]


class MyPolicy:
    """Wraps a ScoreNet so the engine can call it: state + mask -> 12-entry 0/1 vector."""
    def __init__(self, model=None, greedy=True, temperature=1.0, seed=0):
        self.model, self.greedy, self.temperature = model, greedy, temperature
        self.rng = np.random.default_rng(seed)
        self.fallback = rs.RandomPolicy(seed=seed)

    def choose(self, state):
        cands = rs.legal_bids(state)
        with torch.no_grad():
            scores = score_boards(self.model, rs.afterstates(state, cands)).cpu()
        probs = F.softmax(scores / self.temperature, dim=0).numpy().astype(float)
        if self.greedy:
            # Two steps, not a plain argmax: first pass-or-bid by total probability, then the best bid.
            # A plain argmax compares pass with each single bid; with hundreds of bids splitting the
            # "bid" probability, pass often has the top single score even when the policy bids most of
            # the time (measured: argmax passed in 68% of turns where the policy's P(pass) averaged 29%).
            if len(cands) == 1 or probs[0] >= 0.5:      # cands[0] is always pass
                return cands[0]
            return cands[1 + int(scores[1:].argmax())]
        return cands[self.rng.choice(len(cands), p=probs / probs.sum())]

    def bid(self, state, mask):
        if self.model is None:
            return self.fallback.bid(state, mask)
        return rs.candidate_to_vector(self.choose(state))
