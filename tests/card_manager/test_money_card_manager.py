import pytest

from highsociety.code.gamecore.card_manager.money_card_manager import MoneyCardManager
from highsociety.code.gamecore.components_module.money_card import MoneyCard


@pytest.fixture
def manager():
    return MoneyCardManager()


def test_starts_with_configured_denominations(manager):
    values = sorted(c.value for c in manager.cards)
    assert values == [1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25]


def test_total_money_matches_sum_of_cards(manager):
    assert manager.total_money() == sum(c.value for c in manager.cards)


def test_remove_single_card(manager):
    removed = manager.remove_cards(10)
    assert [c.value for c in removed] == [10]
    assert 10 not in [c.value for c in manager.cards]


def test_remove_multiple_cards(manager):
    removed = manager.remove_cards([1, 2, 3])
    assert sorted(c.value for c in removed) == [1, 2, 3]
    remaining = [c.value for c in manager.cards]
    assert 1 not in remaining and 2 not in remaining and 3 not in remaining


def test_remove_missing_card_raises_and_does_not_mutate(manager):
    before = sorted(c.value for c in manager.cards)
    with pytest.raises(ValueError):
        manager.remove_cards(9999)
    after = sorted(c.value for c in manager.cards)
    assert before == after


def test_remove_duplicate_values_raises(manager):
    with pytest.raises(ValueError):
        manager.remove_cards([1, 1])


def test_add_cards_back(manager):
    removed = manager.remove_cards([1, 2])
    total_before = manager.total_money()
    manager.add_cards(removed)
    assert manager.total_money() == total_before + 3
    assert 1 in [c.value for c in manager.cards]
    assert 2 in [c.value for c in manager.cards]


def test_add_cards_rejects_non_moneycard_list(manager):
    with pytest.raises(ValueError):
        manager.add_cards([1, 2, 3])


def test_add_cards_rejects_invalid_type(manager):
    with pytest.raises(ValueError):
        manager.add_cards("not a card")


def test_cards_property_is_immutable_tuple(manager):
    cards = manager.cards
    assert isinstance(cards, tuple)
