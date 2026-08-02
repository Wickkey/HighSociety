"""
Demonstrates the seed+script testing workflow: highsociety/code/gamecore/dev_tools/inspect_seed.py
was used to find and save seed 5 as the "faux_pas_after_early_paintings" scenario
(tests/scenarios/faux_pas_after_early_paintings.json)
because its deck order deals 2 players' worth of paintings before FauxPas comes
up, then a Scandale/Prestige/Passe tail before the green-card cutoff.

Since PlayGame(seed=...) makes the entire game reproducible, the SAME recorded
seed can be replayed with different scripted decisions to produce genuinely
different, deterministic outcomes — one recording, many test cases, no need
to re-discover interesting card orders by hand each time.

Natural (all-pass) turn order for this seed with 2 players:
  Painting(3): alice asked -> passes -> bob wins for free
  Painting(4): bob asked -> passes -> alice wins for free
  Painting(5): alice asked -> passes -> bob wins for free
  FauxPas disgrace auction: bob asked first (bob won the last round, so bob starts)
This is the branch point both tests below diverge from.
"""
import json
from pathlib import Path

from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas

SCENARIO_PATH = Path(__file__).resolve().parents[1] / "scenarios" / "faux_pas_after_early_paintings.json"


def _load_seed():
    return json.loads(SCENARIO_PATH.read_text())["seed"]


def test_scenario_file_exists_and_matches_the_seed_used_below():
    assert SCENARIO_PATH.exists()
    assert _load_seed() == 5


def test_branch_a_bob_takes_faux_pas_and_discards_his_own_painting(make_player):
    """Outcome A: nobody contests the FauxPas auction — bob (who already owns
    paintings from the preceding rounds) takes it and immediately discards one."""
    alice = make_player("Alice", username="alice")
    bob = make_player("Bob", username="bob")

    game = PlayGame(players=[alice, bob], mode="cli", seed=_load_seed())
    game.play_game()

    assert bob.holds_faux_pas is True
    assert bob.has_discarded_card is False  # BasePlayer never flips this back; discard already happened
    # bob won Painting(3) and Painting(5) before FauxPas, discards the lower one (3) first
    assert 3 not in [c.value for c in bob.status_cards]
    assert 5 in [c.value for c in bob.status_cards]


def test_branch_b_bob_raises_and_pushes_faux_pas_onto_alice_instead(make_player):
    """Outcome B: same seed, same preceding rounds — but this time bob raises
    on his very first FauxPas-auction turn instead of passing, forcing alice
    to be the one who ends up taking (and discarding for) the disgrace card."""
    alice = make_player("Alice", username="alice")
    bob = make_player("Bob", username="bob", actions=[[1]])  # raise once, then fall back to "pass"

    game = PlayGame(players=[alice, bob], mode="cli", seed=_load_seed())
    game.play_game()

    assert alice.holds_faux_pas is True
    assert bob.holds_faux_pas is False
    # alice hadn't won any painting yet when she took FauxPas, so nothing to discard immediately
    assert any(isinstance(c, FauxPas) for c in alice.status_cards)
