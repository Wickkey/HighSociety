import json
import socket
import threading
import time

import pytest

from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.network.transport import SocketTransport
from highsociety.code.common.utils.network_utility import send_json
from highsociety.code.gamecore.components_module.painting import Painting


class _LineReader:
    """
    Reads one JSON message at a time from a socket, buffering leftover bytes
    across calls — several send_message() calls in quick succession can
    arrive coalesced into a single recv(), so a reader that discards
    anything past the first line it finds loses messages (and then hangs
    waiting for "new" data that already arrived).
    """

    def __init__(self, sock):
        self._sock = sock
        self._buffer = ""

    def next(self, timeout=2.0):
        self._sock.settimeout(timeout)
        while "\n" not in self._buffer:
            self._buffer += self._sock.recv(4096).decode("utf-8")
        line, self._buffer = self._buffer.split("\n", 1)
        return json.loads(line)


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


def test_get_bid_does_not_crash_on_a_response_without_prompt(player_and_peer):
    """
    Regression test for a web-server crash: a browser client that sends a
    RESPONSE with no `prompt` field (e.g. `{"message_type": "RESPONSE"}`) used
    to raise KeyError inside get_bid(), killing the daemon game thread and
    stranding the room in "in_progress" forever. A missing prompt is invalid
    input, not a crash — send INPUT_ERROR and return None so the caller
    (gameplay.py's _handle_player_turn) re-prompts.
    """
    player, peer = player_and_peer
    reader = _LineReader(peer)

    def answer():
        for _ in range(10):
            msg = reader.next()
            if msg.get("message_type") == "PLAYER_MOVE":
                break
        send_json(peer, {"message_type": "RESPONSE"})  # no prompt field at all
        for _ in range(10):
            msg = reader.next()
            if msg.get("message_type") == "INPUT_ERROR":
                break

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    result = player.get_bid(timeout=5.0)
    t.join(timeout=5.0)

    assert result is None  # invalid input, no exception
    # The caller re-prompts and a well-formed answer is then accepted normally.
    _send_response(peer, "1")
    assert player.get_bid(timeout=2.0) == [1]


def test_get_bid_does_not_crash_on_a_non_string_prompt(player_and_peer):
    """
    Regression test for a web-server crash: a RESPONSE whose `prompt` is a JSON
    list (not a string) used to raise AttributeError on `.lower()`/`.startswith()`.
    Treat it as invalid input and return None (caller re-prompts) instead of
    crashing the game thread.
    """
    player, peer = player_and_peer
    reader = _LineReader(peer)

    def answer():
        for _ in range(10):
            msg = reader.next()
            if msg.get("message_type") == "PLAYER_MOVE":
                break
        send_json(peer, {"message_type": "RESPONSE", "prompt": [1, 2]})
        for _ in range(10):
            msg = reader.next()
            if msg.get("message_type") == "INPUT_ERROR":
                break

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    result = player.get_bid(timeout=5.0)
    t.join(timeout=5.0)

    assert result is None
    _send_response(peer, "1")
    assert player.get_bid(timeout=2.0) == [1]


def test_choose_painting_to_discard_does_not_crash_on_a_non_string_prompt(player_and_peer):
    """
    Regression test for a web-server crash: a non-string discard choice (a JSON
    list) used to raise AttributeError on `.strip()`. Re-prompt instead.
    """
    player, peer = player_and_peer
    player.add_status_card(Painting(value=5))
    reader = _LineReader(peer)

    def answer():
        for _ in range(10):
            msg = reader.next()
            if msg.get("message_type") == "PLAYER_MOVE":
                break
        send_json(peer, {"message_type": "RESPONSE", "prompt": [7]})
        for _ in range(10):
            msg = reader.next()
            if msg.get("message_type") == "INPUT_ERROR":
                break
        send_json(peer, {"message_type": "RESPONSE", "prompt": "5"})

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    chosen = player.choose_painting_to_discard()
    t.join(timeout=5.0)

    assert isinstance(chosen, Painting)
    assert chosen.value == 5


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


def test_bid_prompts_are_sent_with_move_type_bid(player_and_peer):
    player, peer = player_and_peer
    reader = _LineReader(peer)
    result = {}

    def call_get_bid():
        result["bid"] = player.get_bid(timeout=2.0)

    t = threading.Thread(target=call_get_bid, daemon=True)
    t.start()

    # print_player_info() sends several messages before the actual PLAYER_MOVE
    # prompt, so drain until we find it.
    prompt = None
    for _ in range(10):
        msg = reader.next()
        if msg.get("message_type") == "PLAYER_MOVE":
            prompt = msg
            break
    assert prompt is not None
    assert prompt["move_type"] == "bid"

    _send_response(peer, "pass")
    t.join(timeout=2.0)
    assert result["bid"] == "pass"


def test_discard_prompts_are_sent_with_move_type_discard_painting(player_and_peer):
    """
    Regression test: a discard prompt must be distinguishable from a bid
    prompt without parsing the human-readable text — a bot that always
    treats PLAYER_MOVE as a bid request (and answers "pass") would otherwise
    get stuck retrying a discard prompt forever, since "pass" is not a valid
    discard answer.
    """
    player, peer = player_and_peer
    player.add_status_card(Painting(value=5))
    reader = _LineReader(peer)

    def answer():
        prompt = None
        for _ in range(10):
            msg = reader.next()
            if msg.get("message_type") == "PLAYER_MOVE":
                prompt = msg
                break
        assert prompt["move_type"] == "discard_painting"
        _send_response(peer, "5")

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    chosen = player.choose_painting_to_discard()
    t.join(timeout=2.0)

    assert chosen.value == 5
