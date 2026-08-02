"""
Minimal ANSI terminal color/formatting helpers for CLI output.

Automatically falls back to plain, unmodified text when not attached to a
real terminal (piped output, redirected to a file, captured test output) or
when NO_COLOR is set — so scripted/automated consumers never see raw escape
codes, and nothing that currently checks message text needs to change.
"""
import os
import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"


def supports_color(stream=None) -> bool:
    """
    True if `stream` (default sys.stdout) looks like a real terminal that
    understands ANSI escapes. Respects the NO_COLOR / FORCE_COLOR
    conventions (https://no-color.org/) for explicit overrides.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") is not None:
        return True

    stream = stream if stream is not None else sys.stdout
    try:
        return stream.isatty()
    except Exception:
        return False


def colorize(text: str, *styles: str, stream=None) -> str:
    """Wraps text in the given ANSI style codes, unless color is unsupported."""
    if not styles or not supports_color(stream):
        return text
    return "".join(styles) + text + RESET


# Broadcast game-event text (from gameplay.py, via CLIHost or a
# NetworkPlayer/NetworkSpectator's GLOBAL_EVENT message) doesn't carry a
# message_type of its own the way a direct player prompt does — just a plain
# string. This maps the emoji markers gameplay.py already puts at the start
# of these messages to a style, so both the CLI host and the network clients
# can color the exact same broadcast text the same way without duplicating
# the mapping. First match wins.
GAME_EVENT_MARKER_STYLES = (
    ("🏆", (BOLD, GREEN)),
    ("🚀", (BOLD, CYAN)),
    ("🤝", (BOLD, CYAN)),
    ("❌", (RED,)),
    ("⚠️", (YELLOW,)),
    ("😬", (YELLOW,)),
    ("💢", (YELLOW,)),
    ("💀", (MAGENTA,)),
    ("🎨", (MAGENTA,)),
    ("⚪", (DIM,)),
)


def style_game_event(text: str) -> str:
    """Colors broadcast game-event text by its leading emoji marker, if any."""
    for marker, styles in GAME_EVENT_MARKER_STYLES:
        if marker in text:
            return colorize(text, *styles)
    return text
