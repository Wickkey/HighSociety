"""
Bots talking at the table: watches one game's broadcasts, notices the moments
bot_personalities.json has lines for, and has a bot say something.

It only reads what the game already tells every player (the same AUCTION_UPDATE,
AUCTION_RESULT and end-of-game messages a browser gets), so a bot never says
anything that gives away hidden information. Which line, if any, is up to each
bot's BotTalker (talker.py). This file decides *when* a bot may speak:

- one line at a time across the table (TABLE_GAP_SECONDS), and each bot at most
  every BOT_GAP_SECONDS and MAX_LINES_PER_GAME times, so it stays a seasoning;
- a short, human-looking pause before the line appears (DELIVERY_DELAY);
- "slow"/"hesitating" moments are timers on a person's turn that are dropped
  as soon as the turn moves on. Bots don't get them: they never keep anyone
  waiting.

Never raises into the game: observe() swallows its own errors, since a broken
quip must not break a game.
"""
import random
import threading
import time
from typing import Callable, Iterable, Optional

from highsociety.code.ai.personality.talker import BotTalker, TalkMoment
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager

TABLE_GAP_SECONDS = 8.0
BOT_GAP_SECONDS = 25.0
MAX_LINES_PER_GAME = 6
DELIVERY_DELAY = (0.6, 1.6)

CHALLENGE_AFTER_SECONDS = 3.0
HESITATE_AFTER_SECONDS = 8.0
# A slow move: this share of the turn timer, or SLOW_NO_LIMIT_SECONDS with none.
SLOW_SHARE_OF_TURN = 0.6
SLOW_MIN_SECONDS = 12.0
SLOW_NO_LIMIT_SECONDS = 25.0

DISGRACE_TYPES = {"FauxPas", "Passe", "Scandale"}
VALUABLE_PAINTING = 7
STREAK_LENGTH = 3
REPEAT_RAISES = 2
RIVAL_OUTBIDS = 3
COMEBACK_FROM_ROUND = 4


def _default_schedule(delay: float, fn: Callable[[], None]) -> None:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()


class TableTalk:
    def __init__(self, players: Iterable, talkers: dict, is_human: Callable[[object], bool],
                 say: Callable[[str, str], None], turn_duration: Optional[float] = None,
                 schedule: Callable[[float, Callable[[], None]], None] = _default_schedule,
                 clock: Callable[[], float] = time.monotonic, rng: Optional[random.Random] = None):
        """
        talkers: username -> BotTalker, for the bots that talk.
        say(username, text): delivers a line; called from a timer thread.
        """
        self._players = {p.username: p for p in players}
        self._names = {u: getattr(p, "name", u) for u, p in self._players.items()}
        self._humans = {u for u, p in self._players.items() if is_human(p)}
        self._talkers: dict[str, BotTalker] = dict(talkers)
        self._say = say
        self._turn_duration = turn_duration
        self._schedule = schedule
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self._closed = False

        self._last_table_line = float("-inf")
        self._last_bot_line: dict[str, float] = {}
        self._lines_said: dict[str, int] = {}
        self._outbid_by: dict[tuple, int] = {}   # (opponent, bot) -> times opponent raised over bot
        self._streak: dict[str, int] = {}         # bot -> auctions lost in a row that it bid in
        self._was_last: set = set()
        self._comeback_done: set = set()
        self._round = 0
        self._turn_token = 0
        self.closing_line: Optional[dict] = None
        self._reset_auction()

    # -- public ------------------------------------------------------------
    @property
    def has_talkers(self) -> bool:
        return bool(self._talkers)

    def observe(self, message_type: str, data: Optional[dict]) -> None:
        if not data or self._closed:
            return
        try:
            with self._lock:
                if message_type == "AUCTION_UPDATE":
                    self._on_update(data)
                elif message_type == "AUCTION_RESULT":
                    self._on_result(data)
                elif data.get("event") == "countdown_finished":
                    self._speak_one(self._bots_in_random_order(), "game_start")
                elif data.get("event") == "final_standings":
                    self._standings = data.get("standings") or []
                elif data.get("event") == "winner":
                    self._on_game_over(data.get("winners") or [])
        except Exception:
            LoggingManager.exception("Table talk failed on a game message; carrying on")

    def close(self) -> None:
        self._closed = True

    # -- auctions ------------------------------------------------------------
    def _reset_auction(self) -> None:
        self._raises: dict[str, int] = {}
        self._top_bidder: Optional[str] = None
        self._last_kind: Optional[str] = None

    def _on_update(self, data: dict) -> None:
        kind = data.get("kind")
        player = data.get("player")
        if kind == "auction_start":
            self._reset_auction()
            self._round = data.get("round_number") or self._round + 1
            return
        if kind == "turn_start":
            self._turn_token += 1
            if player in self._humans:
                self._watch_turn(player)
            return
        if kind == "bid" and player:
            previous = self._top_bidder
            self._raises[player] = self._raises.get(player, 0) + 1
            if previous and previous != player and previous in self._talkers:
                key = (player, previous)
                self._outbid_by[key] = self._outbid_by.get(key, 0) + 1
            if player in self._talkers:
                self._on_bot_bid(player, previous)
            self._top_bidder = player
        if kind in ("bid", "pass", "fold", "quit"):
            self._last_kind = kind

    def _on_bot_bid(self, bot: str, previous: Optional[str]) -> None:
        moments = []
        opponent = previous if previous and previous != bot else None
        if opponent and self._outbid_by.get((opponent, bot), 0) >= RIVAL_OUTBIDS:
            moments.append("aggressive_rival")
        if opponent and self._raises.get(opponent, 0) >= REPEAT_RAISES:
            moments.append("outbid_repeat_raiser")
        if self._raises.get(bot, 0) >= 3:
            moments.append("keeps_raising")
        self._speak(bot, moments, target=opponent)

    def _watch_turn(self, player: str) -> None:
        token = self._turn_token
        raiser = self._top_bidder if self._last_kind == "bid" and self._top_bidder in self._talkers else None

        def later(delay: float, bots: list, trigger: str) -> None:
            def fire() -> None:
                with self._lock:
                    if self._closed or self._turn_token != token:
                        return
                    self._speak_one(bots, trigger, target=player)
            self._schedule(delay, fire)

        if raiser:
            later(CHALLENGE_AFTER_SECONDS, [raiser], "challenge_opponent")
            later(HESITATE_AFTER_SECONDS, [raiser], "opponent_hesitates")
        if self._turn_duration:
            slow_after = max(SLOW_MIN_SECONDS, self._turn_duration * SLOW_SHARE_OF_TURN)
            if slow_after >= self._turn_duration:
                return
        else:
            slow_after = SLOW_NO_LIMIT_SECONDS
        later(slow_after, self._bots_in_random_order(), "opponent_slow")

    def _on_result(self, data: dict) -> None:
        card = data.get("card") or {}
        recipient = data.get("recipient")
        events = data.get("events") or []
        bidders = {e.get("player") for e in events if e.get("action") == "bid"}
        if card.get("type") in DISGRACE_TYPES:
            if recipient in self._talkers:
                self._speak(recipient, ["took_disgrace"], details={"card": card})
        else:
            if recipient in self._talkers:
                self._streak[recipient] = 0
                moments = []
                if card.get("type") == "PrestigeCard" or (card.get("value") or 0) >= VALUABLE_PAINTING:
                    moments.append("won_valuable")
                moments.append("won_contested" if bidders - {recipient} else "won_uncontested")
                moments.append("won_auction")
                self._speak(recipient, moments, details={"card": card})
            for bot in bidders & set(self._talkers):
                if bot == recipient:
                    continue
                self._streak[bot] = self._streak.get(bot, 0) + 1
                moments = ["losing_streak"] if self._streak[bot] >= STREAK_LENGTH else []
                moments.append("lost_auction")
                if self._speak(bot, moments, target=self._names.get(recipient)) and self._streak[bot] >= STREAK_LENGTH:
                    self._streak[bot] = 0
        self._check_comeback()

    def _check_comeback(self) -> None:
        if self._round < COMEBACK_FROM_ROUND:
            return
        points = {}
        for username, player in self._players.items():
            try:
                points[username] = int(player.points)
            except Exception:
                return
        if len(points) < 2:
            return
        low, high = min(points.values()), max(points.values())
        for bot in self._talkers:
            score = points.get(bot)
            if score is None or bot in self._comeback_done:
                continue
            if score == low and list(points.values()).count(low) == 1:
                self._was_last.add(bot)
            elif bot in self._was_last and score == high and list(points.values()).count(high) == 1:
                self._comeback_done.add(bot)
                self._speak(bot, ["comeback"])

    # -- end of game ---------------------------------------------------------
    def _on_game_over(self, winners: list) -> None:
        standings = {s.get("username"): s for s in getattr(self, "_standings", [])}
        eliminated = {u for u, s in standings.items() if s.get("eliminated")}
        bots = self._bots_in_random_order()
        # Winners first: the result screen shows one closing line, and the
        # winner's is the one worth reading.
        bots.sort(key=lambda b: b not in winners)
        for bot in bots:
            if bot in winners:
                moments = ["game_tied"] if len(winners) > 1 else ["won_game"]
            elif bot in eliminated:
                moments = ["eliminated"]
            else:
                moments = [m for m in ("opponent_eliminated",) if eliminated - {bot}] + ["lost_game"]
            target = self._names.get(next(iter(eliminated), None)) if moments[0] == "opponent_eliminated" else None
            text = self._ask(bot, moments, target=target)
            if text:
                self.closing_line = {"username": bot, "name": self._names.get(bot, bot), "text": text}
                self._say(bot, text)  # now: the table is about to give way to the results
                break
        self._closed = True

    # -- speaking ------------------------------------------------------------
    def _bots_in_random_order(self) -> list:
        bots = list(self._talkers)
        self._rng.shuffle(bots)
        return bots

    def _may_speak(self, bot: str) -> bool:
        now = self._clock()
        return (now - self._last_table_line >= TABLE_GAP_SECONDS
                and now - self._last_bot_line.get(bot, float("-inf")) >= BOT_GAP_SECONDS
                and self._lines_said.get(bot, 0) < MAX_LINES_PER_GAME)

    def _ask(self, bot: str, triggers: list, target: Optional[str] = None,
             details: Optional[dict] = None) -> Optional[str]:
        talker = self._talkers.get(bot)
        if talker is None:
            return None
        for trigger in triggers:
            text = talker.respond(TalkMoment(trigger=trigger, bot_name=self._names.get(bot, bot),
                                             target=target, details=details or {}))
            if text:
                now = self._clock()
                self._last_table_line = now
                self._last_bot_line[bot] = now
                self._lines_said[bot] = self._lines_said.get(bot, 0) + 1
                return text
        return None

    def _speak(self, bot: str, triggers: list, target: Optional[str] = None,
               details: Optional[dict] = None) -> bool:
        if not triggers or self._closed or not self._may_speak(bot):
            return False
        text = self._ask(bot, triggers, target=self._names.get(target, target), details=details)
        if not text:
            return False

        def deliver() -> None:
            self._say(bot, text)
        self._schedule(self._rng.uniform(*DELIVERY_DELAY), deliver)
        return True

    def _speak_one(self, bots: list, trigger: str, target: Optional[str] = None) -> bool:
        return any(self._speak(bot, [trigger], target=target) for bot in bots)
