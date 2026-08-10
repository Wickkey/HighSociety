"""
The default action-picker used everywhere the search *doesn't* explicitly
explore both options via the tree: every opponent's turn (simulation.py's
advance_to_actor always drives them with this, never branches the tree on
their choices), and this player's own turns once a rollout has run past the
tree's frontier (simulation.py's rollout_to_end).

Deliberately just CappedGreedyBot's real, already-tuned heuristic (see
highsociety/code/ai/capped_greedy_bot.py) ported to plain SimState/SimCard
data instead of a live game connection -- reusing an existing, sane bot
model gives more realistic playouts than a uniformly-random one would,
without inventing a second heuristic to maintain.
"""
from highsociety.code.ai.mcts.simulation import SimState

_FLAT_SPEND_LIMITS = {
    "PrestigeCard": 15,
    "FauxPas": 8,
    "Passe": 15,
    "Scandale": 20,
}


def _spend_limit(card_kind: str, card_value: int) -> float:
    if card_kind == "Painting":
        return 3.5 * card_value
    return _FLAT_SPEND_LIMITS.get(card_kind, float("inf"))


def capped_greedy_policy(state: SimState):
    """Raises with the single cheapest card that beats the current bid,
    same as CappedGreedyBot, capped at a per-card-type spend budget so it
    doesn't model opponents as willing to empty their entire hand on one
    card."""
    player = state.players[state.turn]
    needed = state.max_bid - player.current_bid + 1
    limit = _spend_limit(state.current_card.kind, state.current_card.value)
    affordable = [v for v in player.money_cards if v >= needed and player.current_bid + v <= limit]
    if not affordable:
        return "pass"
    return min(affordable)
