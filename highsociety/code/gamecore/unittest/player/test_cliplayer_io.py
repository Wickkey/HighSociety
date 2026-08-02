import os
import sys
import time
import threading

import pytest

from highsociety.code.gamecore.player.cliplayer import CLIPlayer


@pytest.fixture
def piped_player(monkeypatch):
    """
    A CLIPlayer reading from a real OS pipe instead of the terminal, so
    select()-based timeout polling (used by get_bid) behaves exactly as it
    would against a real socket/terminal fd. Yields (player, write_fd).
    """
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(sys, "stdin", os.fdopen(read_fd, "r"))
    player = CLIPlayer(name="Alice", username="alice")
    yield player, write_fd
    try:
        os.close(write_fd)
    except OSError:
        pass


def test_get_bid_returns_none_on_timeout_without_blocking(piped_player):
    player, _write_fd = piped_player
    start = time.time()
    result = player.get_bid(timeout=0.2)
    elapsed = time.time() - start

    assert result is None
    assert elapsed < 1.0  # must not block past the deadline


def test_get_bid_does_not_reprint_prompt_while_polling(piped_player, capsys):
    player, _write_fd = piped_player
    player.get_bid(timeout=0.1)
    capsys.readouterr()  # discard first prompt
    player.get_bid(timeout=0.1)
    out, _ = capsys.readouterr()
    assert "Enter your bid" not in out


def test_get_bid_parses_single_integer(piped_player):
    player, write_fd = piped_player
    os.write(write_fd, b"1\n")
    result = player.get_bid(timeout=1.0)
    assert result == [1]


def test_get_bid_parses_list_of_owned_cards(piped_player):
    player, write_fd = piped_player
    os.write(write_fd, b"[1, 2]\n")
    result = player.get_bid(timeout=1.0)
    assert result == [1, 2]


def test_get_bid_rejects_unowned_card_value(piped_player):
    player, write_fd = piped_player
    os.write(write_fd, b"9999\n")
    result = player.get_bid(timeout=1.0)
    assert result is None


def test_get_bid_recognizes_pass_command(piped_player):
    player, write_fd = piped_player
    os.write(write_fd, b"pass\n")
    result = player.get_bid(timeout=1.0)
    assert result == "pass"


def test_get_bid_reprompts_after_invalid_input_is_consumed(piped_player, capsys):
    player, write_fd = piped_player
    os.write(write_fd, b"not_a_number\n")
    result = player.get_bid(timeout=1.0)
    assert result is None

    capsys.readouterr()
    os.write(write_fd, b"1\n")
    result = player.get_bid(timeout=1.0)
    out, _ = capsys.readouterr()
    assert "Enter your bid" in out  # fresh prompt shown for the retry
    assert result == [1]


def test_get_bid_delivers_input_arriving_after_a_delay(piped_player):
    player, write_fd = piped_player

    def writer():
        time.sleep(0.15)
        os.write(write_fd, b"2\n")

    threading.Thread(target=writer, daemon=True).start()
    start = time.time()
    result = player.get_bid(timeout=2.0)
    elapsed = time.time() - start

    assert result == [2]
    assert elapsed < 1.0


def test_get_bid_returns_quit_and_does_not_spin_when_stdin_closes(piped_player):
    """
    Regression test: sys.stdin.readline() on a closed stream returns '' (EOF)
    instantly rather than blocking, unlike input(). Treating that as "just
    keep polling" (the pre-fix behavior) made get_bid() busy-spin forever the
    moment piped input ran out, instead of ever returning. It must instead be
    treated like a NetworkPlayer disconnect: mark inactive and report "quit".
    """
    player, write_fd = piped_player
    os.close(write_fd)  # close our end -> player's stdin read end sees EOF

    start = time.time()
    result = player.get_bid(timeout=5.0)
    elapsed = time.time() - start

    assert result == "quit"
    assert player.active is False
    assert elapsed < 1.0  # must return immediately, not spin/block for the full timeout


def test_choose_painting_to_discard_returns_none_and_marks_inactive_on_eof(piped_player):
    """
    Regression test: choose_painting_to_discard()'s retry loop used to catch
    EOFError as if it were merely invalid input, looping forever once stdin
    closed instead of ever returning.
    """
    from highsociety.code.gamecore.components_module.painting import Painting

    player, write_fd = piped_player
    player.add_status_card(Painting(value=5))
    os.close(write_fd)

    result = player.choose_painting_to_discard()

    assert result is None
    assert player.active is False
