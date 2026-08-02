import pytest

from highsociety.code.gamecore.components_module.card_factory import CardFactory
from highsociety.code.gamecore.components_module.money_card import MoneyCard
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.prestige_card import PrestigeCard
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas, Passe, Scandale


@pytest.fixture
def factory():
    return CardFactory()


def test_create_money_card(factory):
    card = factory.create_card("money", value=10)
    assert isinstance(card, MoneyCard)
    assert card.value == 10


def test_money_card_is_immutable():
    card = MoneyCard(value=5)
    with pytest.raises(Exception):
        card.value = 10


def test_create_painting_card(factory):
    card = factory.create_card("painting", value=7)
    assert isinstance(card, Painting)
    assert card.value == 7
    assert card.multiplier == 1
    assert card.is_green is False


def test_create_prestige_card(factory):
    card = factory.create_card("prestige")
    assert isinstance(card, PrestigeCard)
    assert card.value == 0
    assert card.multiplier == 2
    assert card.is_green is True


def test_create_disgrace_cards(factory):
    faux_pas = factory.create_card("faux_pas")
    passe = factory.create_card("passe")
    scandale = factory.create_card("scandale")

    assert isinstance(faux_pas, FauxPas)
    assert faux_pas.value == 0 and faux_pas.is_green is False

    assert isinstance(passe, Passe)
    assert passe.value == -5 and passe.is_green is False

    assert isinstance(scandale, Scandale)
    assert scandale.multiplier == 0.5 and scandale.is_green is True


def test_invalid_card_type_raises(factory):
    with pytest.raises(ValueError):
        factory.create_card("not_a_real_card_type")


def test_card_type_is_case_insensitive(factory):
    card = factory.create_card("MONEY", value=3)
    assert isinstance(card, MoneyCard)
