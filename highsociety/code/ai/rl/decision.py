"""
The RL bot's decisions as pure functions of explicit inputs (same pattern as
mcts/stateless_decision.py): given the network and the public game state, the
move doesn't depend on anything else.

Bid: one pass of the network over the current board gives a logit for each of
the 1,024 moves; keep the legal moves' logits (the mask), then decide in two
steps -- pass or bid, then which bid:

    P = softmax(scores)             (the policy the network was trained as)
    pass   if P(pass) >= 0.5
    else   the highest-scored bid

Not a plain argmax over all moves: with hundreds of possible bids splitting
the "bid" probability between them, pass often has the single highest score
even when the policy bids most of the time (measured in training: argmax
passed in 68% of turns where the policy's own P(pass) averaged 29%).
"""
from typing import Optional, Union

import numpy as np

from highsociety.code.ai.rl.candidates import PASS, legal_moves, move_cards
from highsociety.code.ai.rl.encoding import encode
from highsociety.code.ai.rl.network import PolicyNet


def choose_move(net: PolicyNet, board: np.ndarray) -> int:
    """Index into SUBSETS of the chosen move for this board."""
    moves = legal_moves(board)                         # moves[0] is always pass
    if len(moves) == 1:
        return PASS
    scores = net.logits(board[None])[0][moves].astype(np.float64)     # the legal moves' logits (= the mask)
    p = np.exp(scores - scores.max())
    p /= p.sum()
    if p[0] >= 0.5:
        return PASS
    return int(moves[1 + int(scores[1:].argmax())])


def decide_bid(net: PolicyNet, auction_history, event_log: list[dict], live_state: dict,
               username: str) -> Union[list[int], str]:
    """"pass", or the money-card values to add to my bid this turn."""
    move = choose_move(net, encode(auction_history, event_log, live_state, username))
    return "pass" if move == PASS else move_cards(move)


def decide_faux_pas_discard(my_status_cards: list[dict]) -> Optional[int]:
    """Value of the painting to give up for a Faux Pas: always the cheapest. Score
    only grows with total painting value, so this is always optimal -- not a
    network decision (training never asked the network about it either)."""
    return min((c["value"] for c in my_status_cards if c["type"] == "Painting"), default=None)
