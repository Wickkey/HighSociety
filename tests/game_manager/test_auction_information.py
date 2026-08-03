import json

from highsociety.code.gamecore.game_manager.auction_information import AuctionRecord, BidEvent, summarize_card
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.prestige_card import PrestigeCard


def test_summarize_card_is_a_plain_dict():
    summary = summarize_card(Painting(value=7))
    assert summary == {
        "type": "Painting",
        "value": 7,
        "multiplier": 1,
        "is_green": False,
        "description": "Painting Card with value 7",
    }


def test_summarize_card_reflects_the_actual_card_type():
    summary = summarize_card(PrestigeCard())
    assert summary["type"] == "PrestigeCard"
    assert summary["is_green"] is True


def test_add_event_appends_in_order():
    record = AuctionRecord(round_number=1, auction_type="normal", card=summarize_card(Painting(value=5)))
    record.add_event("alice", "bid", 3)
    record.add_event("bob", "bid", 5)
    record.add_event("alice", "pass")

    assert [e.action for e in record.events] == ["bid", "bid", "pass"]
    assert record.events[1].player == "bob"
    assert record.events[1].amount == 5
    assert record.events[2].amount is None


def test_to_dict_is_json_serializable_end_to_end():
    record = AuctionRecord(round_number=2, auction_type="disgrace", card=summarize_card(Painting(value=1)))
    record.add_event("alice", "bid", 4)
    record.add_event("bob", "pass")
    record.recipient = "bob"
    record.price_paid = 0

    payload = record.to_dict()
    reparsed = json.loads(json.dumps(payload))  # would raise if anything weren't JSON-safe

    assert reparsed["round_number"] == 2
    assert reparsed["auction_type"] == "disgrace"
    assert reparsed["recipient"] == "bob"
    assert reparsed["price_paid"] == 0
    assert reparsed["events"] == [
        {"player": "alice", "action": "bid", "amount": 4},
        {"player": "bob", "action": "pass", "amount": None},
    ]


def test_bid_event_to_dict():
    event = BidEvent(player="alice", action="bid", amount=10)
    assert event.to_dict() == {"player": "alice", "action": "bid", "amount": 10}
