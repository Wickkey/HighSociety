from highsociety.code.ai.mcts.policy import capped_greedy_policy
from highsociety.code.ai.mcts.search import MCTSConfig, run_search
from highsociety.code.ai.mcts.simulation import SimCard, SimPlayer, SimState, legal_actions


def _painting(value):
    return SimCard(kind="Painting", value=value, multiplier=1, is_green=False)


def _make_state(hands, current_card, turn=0, max_bid=0, deck=None):
    players = [SimPlayer(username=name, money_cards=sorted(cards)) for name, cards in hands.items()]
    return SimState(
        players=players, turn=turn, deck=list(deck or []), current_card=current_card,
        is_disgrace=False, max_bid=max_bid, still_in=[True] * len(players), green_count=0,
    )


_TINY_CONFIG = MCTSConfig(iterations=15, determinizations=2, exploration_constant=1.4)


def test_returns_a_legal_action():
    state = _make_state({"me": [1, 5, 10], "opp": [3]}, _painting(5))
    action = run_search(state, me_idx=0, rollout_policy=capped_greedy_policy, config=_TINY_CONFIG)
    assert action in legal_actions(state)


def test_only_one_choice_available_returns_it_without_searching_further():
    # "me" holds nothing at all -- "pass" is the only legal action.
    state = _make_state({"me": [], "opp": [3]}, _painting(5))
    action = run_search(state, me_idx=0, rollout_policy=capped_greedy_policy, config=_TINY_CONFIG)
    assert action == "pass"

    # Wiring check for the zero-iterations edge case specifically (no tree
    # ever gets built at all -- run_search must still return *some* legal
    # action rather than crashing or returning None).
    zero_config = MCTSConfig(iterations=0, determinizations=1)
    action = run_search(state, me_idx=0, rollout_policy=capped_greedy_policy, config=zero_config)
    assert action == "pass"


def test_does_not_hand_the_opponent_a_free_win_by_passing():
    """The opponent is completely out of money (nothing left to raise with
    at all) and this is the last card in the game (empty deck -- nothing
    left for spending less now to help with later). Passing myself would
    hand *them* the card for free (normal-auction rule: the last still-in
    bidder wins) and lose outright; any raise instead guarantees the
    opponent must pass next turn (no legal raise of their own) and I win.
    A reasonable search must never blunder into the strictly losing option
    when a guaranteed win is sitting right there."""
    state = _make_state({"me": [1, 25], "opp": []}, _painting(5), deck=[])
    config = MCTSConfig(iterations=60, determinizations=3, exploration_constant=1.4)
    action = run_search(state, me_idx=0, rollout_policy=capped_greedy_policy, config=config)
    assert action != "pass"


def test_root_children_cover_every_legal_action_given_enough_iterations():
    """With enough iterations relative to the branching factor, expansion
    should get around to trying every legal action from the root at least
    once (this is what makes the aggregated vote meaningful in the first
    place -- an action that's never been tried can't win)."""
    state = _make_state({"me": [1, 2, 4], "opp": [3]}, _painting(5))
    config = MCTSConfig(iterations=50, determinizations=1, exploration_constant=1.4)
    # Peek at the internal tree via the module's own helper to confirm
    # every legal action got expanded at least once.
    from highsociety.code.ai.mcts.search import _run_one_tree
    visits_by_action = _run_one_tree(state, me_idx=0, rollout_policy=capped_greedy_policy, config=config)
    assert set(visits_by_action.keys()) == set(legal_actions(state))
