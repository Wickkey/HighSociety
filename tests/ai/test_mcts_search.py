import random

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
    """Last card in the game (empty deck). "broke" has no money left but is
    still in this auction; "rich" already dropped out and holds a Painting
    3. Passing hands the Painting 5 to broke for free -- broke is then the
    poorest and eliminated, and rich's 3 points beat my 0, so I lose. Any
    raise leaves broke unable to answer, I take the 5 and win.

    (This used to be a 2-player version with a broke opponent, but there
    the broke player is eliminated whatever happens, so passing won too;
    the old test only held because ties always went against "pass".)"""
    players = [SimPlayer("me", [1, 25]), SimPlayer("broke", []),
               SimPlayer("rich", [10, 30], status_cards=[_painting(3)])]
    state = SimState(players=players, turn=0, deck=[], current_card=_painting(5), is_disgrace=False,
                     max_bid=0, still_in=[True, True, False], green_count=0)
    config = MCTSConfig(iterations=60, determinizations=3, exploration_constant=1.4)
    for seed in range(20):
        action = run_search(state, 0, capped_greedy_policy, config, random.Random(seed))
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


_FULL_HAND = [1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25]


def test_ties_are_broken_at_random_not_toward_the_largest_card(monkeypatch):
    """Every playout scores the same here, so every decision is one big
    tie. Ties used to go to whichever action was expanded first -- always
    the largest card -- which is how easy bots came to open a Painting 1
    with their 25 (rl-v1 self-play data). They must now spread out."""
    from highsociety.code.ai.mcts import search
    monkeypatch.setattr(search, "evaluate_reward", lambda state, me_idx: 0.0)
    state = _make_state({"me": _FULL_HAND, "opp": _FULL_HAND}, _painting(1),
                        deck=[_painting(v) for v in (2, 3, 4)])
    config = MCTSConfig(iterations=20, determinizations=2, exploration_constant=1.4)
    picks = [run_search(state, 0, capped_greedy_policy, config, random.Random(seed)) for seed in range(120)]
    assert len(set(picks)) >= 8
    assert picks.count(25) < 30


def test_the_same_rng_seed_gives_the_same_decision():
    state = _make_state({"me": _FULL_HAND, "opp": _FULL_HAND}, _painting(4),
                        deck=[_painting(v) for v in (1, 2, 3, 5, 6)])
    config = MCTSConfig(iterations=40, determinizations=3, exploration_constant=1.4)
    first = [run_search(state, 0, capped_greedy_policy, config, random.Random(seed)) for seed in range(10)]
    again = [run_search(state, 0, capped_greedy_policy, config, random.Random(seed)) for seed in range(10)]
    assert first == again


def test_each_determinization_searches_its_own_deck_order(monkeypatch):
    """The caller samples one deck order to build the root state; every
    determinization used to search that same order, so 8 of them were one
    future searched 8 times."""
    from highsociety.code.ai.mcts import search
    seen = []
    real_run_one_tree = search._run_one_tree

    def spy(root_state, *args, **kwargs):
        seen.append(tuple(card.value for card in root_state.deck))
        return real_run_one_tree(root_state, *args, **kwargs)

    monkeypatch.setattr(search, "_run_one_tree", spy)
    deck = [_painting(v) for v in range(1, 11)]
    state = _make_state({"me": [1, 5, 10], "opp": [3]}, _painting(5), deck=deck)
    run_search(state, 0, capped_greedy_policy, MCTSConfig(iterations=5, determinizations=6), random.Random(0))
    assert len(seen) == 6
    assert len(set(seen)) > 1
    assert all(sorted(order) == list(range(1, 11)) for order in seen)   # same cards, new order
    assert [card.value for card in state.deck] == list(range(1, 11))   # caller's state untouched
