import io

from highsociety.code.common.utils.terminal_colors import colorize, supports_color, RED, BOLD


class _FakeStream:
    def __init__(self, is_tty):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


def test_supports_color_false_for_a_non_tty_stream(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert supports_color(_FakeStream(False)) is False


def test_supports_color_true_for_a_tty_stream(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert supports_color(_FakeStream(True)) is True


def test_no_color_env_var_disables_color_even_on_a_tty(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert supports_color(_FakeStream(True)) is False


def test_force_color_env_var_enables_color_even_off_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert supports_color(_FakeStream(False)) is True


def test_colorize_wraps_text_when_color_is_supported(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = colorize("hello", RED, BOLD)
    assert result.startswith(RED) or result.startswith(BOLD)
    assert "hello" in result
    assert result.endswith("\033[0m")


def test_colorize_returns_plain_text_when_color_is_unsupported(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    result = colorize("hello", RED, BOLD)
    assert result == "hello"


def test_colorize_returns_plain_text_when_no_styles_given(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = colorize("hello")
    assert result == "hello"
