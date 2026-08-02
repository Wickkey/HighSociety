import socket
import threading
import time

import pytest

from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.network.transport import SocketTransport
from highsociety.code.common.utils.network_utility import send_json
from highsociety.code.gamecore.components_module.painting import Painting


@pytest.fixture
def player_and_peer():
    server_end, client_end = socket.socketpair()
    transport = SocketTransport(server_end, label="alice")
    player = NetworkPlayer(name="Alice", username="alice", transport=transport, game_id="g1")
    player.start_receiver_thread()
    yield player, client_end
    player.close()
    client_end.close()


def _send_response(peer_socket, text):
    send_json(peer_socket, {"message_type": "RESPONSE", "prompt": text})


def test_get_bid_parses_single_integer_over_the_socket(player_and_peer):
    player, peer = player_and_peer
    _send_response(peer, "1")
    result = player.get_bid(timeout=2.0)
    assert result == [1]


def test_get_bid_parses_list_of_owned_cards_over_the_socket(player_and_peer):
    player, peer = player_and_peer
    _send_response(peer, "[1, 2, 4]")
    result = player.get_bid(timeout=2.0)
    assert result == [1, 2, 4]


def test_get_bid_parses_pass_command(player_and_peer):
    player, peer = player_and_peer
    _send_response(peer, "pass")
    result = player.get_bid(timeout=2.0)
    assert result == "pass"


def test_get_bid_rejects_unowned_money_card(player_and_peer):
    player, peer = player_and_peer
    _send_response(peer, "9999")
    result = player.get_bid(timeout=2.0)
    assert result is None


def test_ping_updates_heartbeat_and_is_not_delivered_as_a_bid(player_and_peer):
    # NOTE: uses threading.Event().wait() instead of time.sleep() — the
    # session's autouse fixture monkeypatches time.sleep to a no-op (to skip
    # the CLI's countdown delay in other tests), which would make any real
    # wait here a no-op too.
    player, peer = player_and_peer
    threading.Event().wait(0.05)  # ensure a measurable gap from the constructor's initial timestamp
    before = player.get_last_heartbeat()

    send_json(peer, {"message_type": "PING", "prompt": ""})
    threading.Event().wait(0.2)
    assert player.get_last_heartbeat() > before

    # A real bid sent afterwards should still be the next thing get_bid sees
    # (PING must not have been queued ahead of it).
    _send_response(peer, "2")
    result = player.get_bid(timeout=2.0)
    assert result == [2]


def test_choose_painting_to_discard_parses_the_response_dict_correctly(player_and_peer):
    """
    Regression test: _get_message_from_queue returns the raw {"prompt": ...}
    dict, not a string. choose_painting_to_discard used to call .strip()
    directly on that dict, raising AttributeError (uncaught by the
    surrounding except (ValueError, KeyError) clause) for every network
    player asked to discard a painting.
    """
    player, peer = player_and_peer
    player.add_status_card(Painting(value=5))
    player.add_status_card(Painting(value=7))

    _send_response(peer, "7")
    chosen = player.choose_painting_to_discard()

    assert isinstance(chosen, Painting)
    assert chosen.value == 7


def test_choose_painting_to_discard_reprompts_on_invalid_choice(player_and_peer):
    player, peer = player_and_peer
    player.add_status_card(Painting(value=5))

    _send_response(peer, "not_a_number")
    _send_response(peer, "5")
    chosen = player.choose_painting_to_discard()

    assert chosen.value == 5


def test_get_bid_returns_quit_when_connection_closes_while_inactive(player_and_peer):
    player, peer = player_and_peer
    peer.close()  # triggers the receiver thread to mark the player inactive
    result = player.get_bid(timeout=2.0)
    assert result == "quit"


def test_get_bid_ignores_a_message_with_mismatched_game_id(player_and_peer):
    """
    A message tagged with a *different, present* game_id must not be treated
    as this turn's input — it belongs to another game (a confused client, or
    stale data), not this one. A missing game_id stays permissive.
    """
    player, peer = player_and_peer  # fixture's player has game_id="g1"

    send_json(peer, {"game_id": "some-other-game", "message_type": "RESPONSE", "prompt": "1"})
    result = player.get_bid(timeout=0.3)
    assert result is None  # discarded, not delivered as a bid

    # the real response, correctly tagged, is still picked up normally afterwards
    send_json(peer, {"game_id": "g1", "message_type": "RESPONSE", "prompt": "1"})
    result = player.get_bid(timeout=2.0)
    assert result == [1]


def test_get_bid_accepts_a_message_with_no_game_id(player_and_peer):
    """Permissive on a missing game_id — lightweight clients/tests aren't required to set it."""
    player, peer = player_and_peer
    send_json(peer, {"message_type": "RESPONSE", "prompt": "pass"})
    result = player.get_bid(timeout=2.0)
    assert result == "pass"


def test_choose_painting_to_discard_ignores_a_message_with_mismatched_game_id(player_and_peer):
    player, peer = player_and_peer
    player.add_status_card(Painting(value=5))

    send_json(peer, {"game_id": "some-other-game", "message_type": "RESPONSE", "prompt": "5"})
    send_json(peer, {"game_id": "g1", "message_type": "RESPONSE", "prompt": "5"})

    chosen = player.choose_painting_to_discard()
    assert chosen.value == 5


def test_send_message_marks_the_player_inactive_on_a_dead_connection(player_and_peer):
    """
    Regression test: a failed send used to only print a warning, never
    marking the player inactive — so every future broadcast (e.g. "X's turn"
    notifications to every other player each round) kept retrying and
    failing against the same dead connection instead of learning once that
    it's gone and skipping it from then on.
    """
    player, peer = player_and_peer
    peer.close()

    assert player.active is True
    player.send_message("hello", message_type="GLOBAL_EVENT")
    assert player.active is False


def test_send_message_only_prints_the_warning_once(player_and_peer, capsys):
    player, peer = player_and_peer
    peer.close()

    player.send_message("first", message_type="GLOBAL_EVENT")
    capsys.readouterr()  # discard the first (expected) warning
    player.send_message("second", message_type="GLOBAL_EVENT")
    out, _ = capsys.readouterr()
    assert "Connection lost" not in out
