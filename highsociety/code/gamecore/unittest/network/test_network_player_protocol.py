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
