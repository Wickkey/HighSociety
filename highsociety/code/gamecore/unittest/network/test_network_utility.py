import socket
import threading
import time

import pytest

from highsociety.code.common.utils.network_utility import send_json, receive_json


@pytest.fixture
def socket_pair():
    a, b = socket.socketpair()
    yield a, b
    a.close()
    b.close()


def test_send_then_receive_roundtrip(socket_pair):
    a, b = socket_pair
    send_json(a, {"message_type": "PING", "prompt": "hello"})
    result = receive_json(b)
    assert result == {"message_type": "PING", "prompt": "hello"}


def test_receive_json_buffers_a_message_split_across_writes(socket_pair):
    a, b = socket_pair
    payload = '{"message_type": "RESPONSE", "prompt": "10"}\n'
    # Simulate TCP segmentation: write the message in two separate chunks.
    a.sendall(payload[:20].encode("utf-8"))
    threading.Thread(target=lambda: (time.sleep(0.1), a.sendall(payload[20:].encode("utf-8")))).start()

    result = receive_json(b)
    assert result == {"message_type": "RESPONSE", "prompt": "10"}


def test_receive_json_raises_when_connection_closes_without_data(socket_pair):
    a, b = socket_pair
    a.close()
    with pytest.raises(ConnectionError):
        receive_json(b)


def test_send_json_appends_newline_delimiter(socket_pair):
    a, b = socket_pair
    send_json(a, {"x": 1})
    raw = b.recv(4096).decode("utf-8")
    assert raw.endswith("\n")
