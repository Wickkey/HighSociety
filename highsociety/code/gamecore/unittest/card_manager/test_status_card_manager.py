from highsociety.code.gamecore.card_manager.status_card_manager import StatusCardManager
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.prestige_card import PrestigeCard
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas, Passe, Scandale


def test_deck_matches_configured_composition():
    manager = StatusCardManager()
    cards = []
    while not manager.is_empty():
        cards.append(manager.remove_top_card())

    assert len(cards) == 16
    assert sorted(c.value for c in cards if isinstance(c, Painting)) == list(range(1, 11))
    assert sum(isinstance(c, PrestigeCard) for c in cards) == 3
    assert sum(isinstance(c, FauxPas) for c in cards) == 1
    assert sum(isinstance(c, Passe) for c in cards) == 1
    assert sum(isinstance(c, Scandale) for c in cards) == 1


def test_is_empty_and_count_track_removals():
    manager = StatusCardManager()
    start_count = manager.get_card_count()
    assert start_count == 16
    assert manager.is_empty() is False

    manager.remove_top_card()
    assert manager.get_card_count() == start_count - 1


def test_remove_from_empty_deck_raises():
    manager = StatusCardManager()
    for _ in range(manager.get_card_count()):
        manager.remove_top_card()

    assert manager.is_empty()
    import pytest
    with pytest.raises(IndexError):
        manager.remove_top_card()


def test_each_instance_has_its_own_independent_deck():
    """
    Regression test: StatusCardManager used to be a process-wide singleton,
    so a second instance would silently reuse the first one's (possibly
    already-consumed) deck instead of getting a fresh shuffle.
    """
    first = StatusCardManager()
    first.remove_top_card()
    first.remove_top_card()
    assert first.get_card_count() == 14

    second = StatusCardManager()
    assert second.get_card_count() == 16
    assert second is not first
