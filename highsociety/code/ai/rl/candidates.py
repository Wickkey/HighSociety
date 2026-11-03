"""
The moves the network chooses between, and the board after each one.

The network doesn't output a bid directly. Each legal move is written into the
board ("afterstate": the table as it would look right after that move), the
network scores every afterstate, and the policy picks among the scores.

A move is an index into SUBSETS: the set of money cards it ADDS this turn.
Index 0 is the empty set -- pass. At most MAX_BID_CARDS cards may be on the
table in one auction (bids of more cards are almost never right, and capping
it keeps the move list at <= 1,024).

Must match .RL/rl_seat.py (legal_bids / afterstates), which training used.
"""
from itertools import combinations

import numpy as np

from highsociety.code.ai.rl.encoding import GAME_COL, HIGH_BIDDER0, IN_AUCTION0, MAX_PLAYERS, MONEY, N_MONEY

MAX_BID_CARDS = 5
PASS = 0                                               # SUBSETS[0]: add nothing

SUBSETS = np.array([[i in c for i in range(N_MONEY)]
                    for n in range(MAX_BID_CARDS + 1)
                    for c in combinations(range(N_MONEY), n)], dtype=np.int8)   # (1024, 11)
SUBSET_SIZE = SUBSETS.sum(1)
SUBSET_VALUE = SUBSETS.astype(int) @ np.array(MONEY)
_MONEY = np.array(MONEY)


def bid_totals(board: np.ndarray) -> tuple[int, int]:
    """(my money on the table, the highest bid on the table) in this auction."""
    totals = ((board[:N_MONEY, :MAX_PLAYERS] == -1) * _MONEY[:, None]).sum(0)
    return int(totals[0]), int(totals.max())


def legal_moves(board: np.ndarray) -> np.ndarray:
    """Indices into SUBSETS: pass (always first), then every set of cards from my
    hand that beats the high bid and keeps at most MAX_BID_CARDS on the table."""
    hand = board[:N_MONEY, 0] == 1
    n_table = int((board[:N_MONEY, 0] == -1).sum())
    mine, top = bid_totals(board)
    ok = ((SUBSETS[:, ~hand].sum(1) == 0)
          & (SUBSET_SIZE + n_table <= MAX_BID_CARDS)
          & (mine + SUBSET_VALUE > top))
    ok[PASS] = True
    return np.flatnonzero(ok)


def afterstates(board: np.ndarray, moves: np.ndarray) -> np.ndarray:
    """(K, 27, 6): the board right after each move."""
    after = np.repeat(board[None], len(moves), axis=0)
    after[:, :N_MONEY, 0] -= 2 * SUBSETS[moves]                      # hand (1) -> on the table (-1)
    is_pass = moves == PASS
    after[is_pass, IN_AUCTION0, GAME_COL] = 0                        # pass: I'm out of this auction
    bid = ~is_pass
    after[bid, HIGH_BIDDER0:HIGH_BIDDER0 + MAX_PLAYERS, GAME_COL] = 0
    after[bid, HIGH_BIDDER0, GAME_COL] = 1                           # bid: I hold the high bid
    return after


def move_cards(move: int) -> list[int]:
    """The money-card values a move adds (empty for pass)."""
    return [MONEY[i] for i in np.flatnonzero(SUBSETS[move])]
