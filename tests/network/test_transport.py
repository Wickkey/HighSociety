import socket
import threading
import time

import pytest

from highsociety.code.gamecore.network.transport import SocketTransport

# NOTE: this file uses threading.Event().wait() instead of time.sleep() for
# real delays — the session's autouse fixture monkeypatches time.sleep to a
# no-op (to skip the CLI's countdown delay in other tests), which would turn
# these polling waits into busy-spins instead of real waits.


@pytest.fixture
def transport_pair():
    server_end, client_end = socket.socketpair()
    transport = SocketTransport(server_end, label="test")
    transport.start()
    yield transport, client_end
    transport.close()
    client_end.close()


def _send(peer_socket, payload):
    import json
    peer_socket.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def test_send_writes_json_to_the_socket(transport_pair):
    transport, peer = transport_pair
    transport.send({"message_type": "GLOBAL_EVENT", "prompt": "hello"})
    raw = peer.recv(4096).decode("utf-8")
    assert '"prompt": "hello"' in raw
    assert raw.endswith("\n")


def test_receive_delivers_a_sent_message(transport_pair):
    transport, peer = transport_pair
    _send(peer, {"message_type": "RESPONSE", "prompt": "pass"})
    result = transport.receive(timeout=2.0)
    assert result == {"message_type": "RESPONSE", "prompt": "pass"}


def test_receive_returns_none_on_timeout_without_blocking(transport_pair):
    transport, _peer = transport_pair
    start = time.time()
    result = transport.receive(timeout=0.2)
    elapsed = time.time() - start

    assert result is None
    assert elapsed < 1.0


def test_ping_updates_heartbeat_and_is_filtered_out_of_receive(transport_pair):
    transport, peer = transport_pair
    before = transport.get_last_heartbeat()

    _send(peer, {"message_type": "PING", "prompt": ""})
    # give the receiver thread a moment to process it
    deadline = time.time() + 2.0
    while transport.get_last_heartbeat() == before and time.time() < deadline:
        threading.Event().wait(0.02)

    assert transport.get_last_heartbeat() > before

    _send(peer, {"message_type": "RESPONSE", "prompt": "2"})
    result = transport.receive(timeout=2.0)
    assert result == {"message_type": "RESPONSE", "prompt": "2"}  # PING never queued ahead of it


def test_is_connected_becomes_false_when_peer_closes(transport_pair):
    transport, peer = transport_pair
    assert transport.is_connected is True

    peer.close()

    deadline = time.time() + 2.0
    while transport.is_connected and time.time() < deadline:
        threading.Event().wait(0.02)

    assert transport.is_connected is False


def test_receive_returns_none_after_peer_closes(transport_pair):
    transport, peer = transport_pair
    peer.close()
    result = transport.receive(timeout=2.0)
    assert result is None
