"""
What a bot says when something happens at the table.

BotTalker is the plug: given a moment (a trigger name plus what happened),
return a line or None. TableTalk (table_talk.py) works out the moments and
delivers the lines; it doesn't care how a talker picks them. Today every bot
uses ScriptedTalker, which picks from bot_personalities.json with chances and
cooldowns. A language-model talker can drop in later: implement `respond`,
and have `make_talker` return it (see TALKER_FACTORY).
"""
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

PERSONALITIES_PATH = os.path.join(os.path.dirname(__file__), "bot_personalities.json")


@dataclass
class TalkMoment:
    """One thing that happened, from one bot's point of view."""
    trigger: str                     # a key of bot_personalities.json's "triggers"
    bot_name: str                    # the bot's seat name, e.g. "Thanos Bot"
    target: Optional[str] = None     # the other player involved, by display name
    details: dict = field(default_factory=dict)  # e.g. {"card": {...}, "bid": 12}


class BotTalker:
    """Interface: one per bot per game, so it can remember what it said."""

    def respond(self, moment: TalkMoment) -> Optional[str]:
        raise NotImplementedError


@dataclass
class _Line:
    text: str
    triggers: frozenset
    chance: float
    max_per_game: int
    cooldown_seconds: float
    said: int = 0
    last_said_at: float = float("-inf")


class ScriptedTalker(BotTalker):
    """Fixed lines, each tied to triggers. Of the lines that fit a moment and
    are off cooldown, one is chosen and then has its own chance of being said,
    so the same moment doesn't always get a reply."""

    def __init__(self, lines: list[dict], defaults: dict, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._rng = rng or random.Random()
        self._clock = clock
        self._lines = [
            _Line(text=entry["text"], triggers=frozenset(entry["triggers"]),
                  chance=float(entry.get("chance", defaults.get("chance", 0.5))),
                  max_per_game=int(entry.get("max_per_game", defaults.get("max_per_game", 1))),
                  cooldown_seconds=float(entry.get("cooldown_seconds", defaults.get("cooldown_seconds", 120))))
            for entry in lines
        ]

    def triggers(self) -> frozenset:
        return frozenset().union(*(line.triggers for line in self._lines)) if self._lines else frozenset()

    def respond(self, moment: TalkMoment) -> Optional[str]:
        now = self._clock()
        ready = [line for line in self._lines
                 if moment.trigger in line.triggers and line.said < line.max_per_game
                 and now - line.last_said_at >= line.cooldown_seconds]
        if not ready:
            return None
        line = self._rng.choice(ready)
        if self._rng.random() >= line.chance:
            return None
        line.said += 1
        line.last_said_at = now
        return line.text


_personalities_cache: Optional[dict] = None


def load_personalities(path: str = PERSONALITIES_PATH) -> dict:
    global _personalities_cache
    if path != PERSONALITIES_PATH:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if _personalities_cache is None:
        with open(path, encoding="utf-8") as f:
            _personalities_cache = json.load(f)
    return _personalities_cache


def scripted_talker(bot_key: str, rng: Optional[random.Random] = None) -> Optional[BotTalker]:
    data = load_personalities()
    bot = data["bots"].get(bot_key)
    if not bot or not bot.get("lines"):
        return None
    return ScriptedTalker(bot["lines"], data.get("defaults", {}), rng=rng)


# Swap this for another factory (same signature) to change how every bot
# talks, e.g. a language-model talker that falls back to scripted_talker.
TALKER_FACTORY: Callable[[str], Optional[BotTalker]] = scripted_talker


def make_talker(bot_key: str) -> Optional[BotTalker]:
    """The talker for a roster bot, or None if it has nothing to say."""
    return TALKER_FACTORY(bot_key)
