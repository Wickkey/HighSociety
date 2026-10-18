"""
A self-contained, JSON-serializable record of one whole game -- enough to
rebuild it move for move with nothing else (no live players, no database
joins, no engine RNG state) -- plus the replayer that does exactly that and
checks it reaches the identical outcome.

Why this exists alongside game_history.py's game_rounds/round_players/
game_actions tables: those are shaped for the website's stats/history views,
and turned out NOT to be enough to reproduce a game (audited against
production): no seed, no seat order or starting player, two same-difficulty
bots share one player_id so their actions can't be told apart, a bid is
stored as an amount (not the money cards behind it, which changes what the
player can bid later), and Faux Pas discards aren't stored at all. This log
records all of that directly, keyed by *seat*, which is also the shape an RL
pipeline wants: one row is one complete, replayable episode.

Format (REPLAY_FORMAT_VERSION 1):
    {
      "version": 1,
      "seed": 123,                       # PlayGame's own per-game RNG seed
      "turn_duration": null,             # seconds per move, null = untimed
      "green_card_limit": 4,
      "settlement": "ForfeitSettlement",
      "players": [                       # order handed to random.shuffle --
        {"username", "name", "kind"},    # replaying needs this exact order
      ],
      "seats": ["u1", ...],              # realized turn order after the shuffle
      "starting_seat": 2,
      "deck": [{"type", "value"}, ...],  # full realized draw order, top first
      "decisions": [                     # every decision, in global order
        {"seat": 0, "kind": "bid", "result": [3, 4]},  # raw get_bid output
        {"seat": 1, "kind": "bid", "result": "pass", "timed_out": true},
        {"seat": 2, "kind": "discard", "result": 7, "active_after": true},
        {"seat": 3, "kind": "inactive"},  # engine found the seat disconnected
      ],
      "rounds": [{"round", "card", "auction_type", "recipient_seat", "price"}],
      "result": {"standings": [{"seat", "points", "money_left", "active",
                                 "eliminated"}], "winner_seats": [0]}
    }

`kind` per player is the bot type key from highsociety.code.ai.BOT_TYPES
("easy"/"greedy"/...), "tutorial" for the tutorial bot, or "human".

Known limitation: a human who disconnects mid-game goes inactive
asynchronously, and the engine reads `active` in several places that don't
correspond to any decision -- so a log containing an "inactive" decision is
recorded faithfully but replay_game() refuses it (ReplayUnsupported) rather
than pretend to reproduce it. Bot-only games, and humans who play to the end
or quit explicitly (a "quit" is an ordinary recorded decision), replay
exactly.
"""
import contextlib
import time
from typing import Optional

REPLAY_FORMAT_VERSION = 1


class ReplayMismatch(RuntimeError):
    """The rebuilt game diverged from what the log says happened."""


class ReplayUnsupported(RuntimeError):
    """The log is valid but describes something replay can't reproduce
    (see the module docstring's known limitation)."""


def _player_kind(player) -> str:
    # Imported lazily: highsociety.code.ai imports gameplay-adjacent modules,
    # and gameplay.py imports this one.
    from highsociety.code.ai import BOT_TYPES
    from highsociety.code.ai.tutorial_bot import TutorialBot
    from highsociety.code.gamecore.player.cliplayer import CLIPlayer
    from highsociety.code.gamecore.player.networkplayer import NetworkPlayer

    if isinstance(player, (CLIPlayer, NetworkPlayer)):
        return "human"
    if isinstance(player, TutorialBot):
        return "tutorial"
    for key, cls in BOT_TYPES.items():
        if type(player) is cls:
            return key
    return type(player).__name__


class GameReplayLog:
    """Filled in by PlayGame as the game runs -- see gameplay.py's call
    sites. Every method is a cheap append/assignment; nothing here can
    affect the game itself."""

    def __init__(self, seed: int, turn_duration: Optional[float], green_card_limit: int, settlement: str):
        self.seed = seed
        self.turn_duration = turn_duration
        self.green_card_limit = green_card_limit
        self.settlement = settlement
        self.players: list[dict] = []
        self.seats: list[str] = []
        self.starting_seat: Optional[int] = None
        self.deck: list[dict] = []
        self.decisions: list[dict] = []
        self.rounds: list[dict] = []
        self.result: Optional[dict] = None

    def set_deck(self, cards) -> None:
        self.deck = [{"type": type(c).__name__, "value": c.value} for c in cards]

    def set_pre_shuffle_order(self, players) -> None:
        self.players = [{"username": p.username, "name": p.name, "kind": _player_kind(p)} for p in players]

    def set_seats(self, players) -> None:
        self.seats = [p.username for p in players]

    def set_starting_seat(self, seat: int) -> None:
        self.starting_seat = seat

    def add_bid_decision(self, seat: int, result, timed_out: bool = False) -> None:
        entry = {"seat": seat, "kind": "bid", "result": list(result) if isinstance(result, (list, tuple)) else result}
        if timed_out:
            entry["timed_out"] = True
        self.decisions.append(entry)

    def add_discard_decision(self, seat: int, value: Optional[int], active_after: bool) -> None:
        self.decisions.append({"seat": seat, "kind": "discard", "result": value, "active_after": active_after})

    def add_inactive_turn(self, seat: int) -> None:
        self.decisions.append({"seat": seat, "kind": "inactive"})

    def set_result(self, players, final_standings: list[dict], winners, auction_rounds) -> None:
        seat_of = {p.username: i for i, p in enumerate(players)}
        self.rounds = []
        for record in auction_rounds:
            recipient = record.recipient
            self.rounds.append({
                "round": record.round_number,
                "card": {"type": record.card["type"], "value": record.card["value"]},
                "auction_type": record.auction_type,
                "recipient_seat": seat_of.get(recipient) if recipient else None,
                "price": record.money_spent.get(recipient, 0) if recipient else 0,
            })
        self.result = {
            "standings": [
                {"seat": seat_of[s["username"]], "points": s["points"], "money_left": s["money_left"],
                 "active": s["active"], "eliminated": s["eliminated"]}
                for s in final_standings
            ],
            "winner_seats": sorted(seat_of[w.username] for w in (winners or [])),
        }

    def to_dict(self) -> dict:
        return {
            "version": REPLAY_FORMAT_VERSION,
            "seed": self.seed,
            "turn_duration": self.turn_duration,
            "green_card_limit": self.green_card_limit,
            "settlement": self.settlement,
            "players": self.players,
            "seats": self.seats,
            "starting_seat": self.starting_seat,
            "deck": self.deck,
            "decisions": self.decisions,
            "rounds": self.rounds,
            "result": self.result,
        }


class _Cursor:
    """The one shared, global-order decision queue every replay seat reads
    from -- global (not per-seat) so replay also proves the engine asked
    the seats in the same order as the original game did."""

    def __init__(self, decisions: list[dict]):
        self.decisions = decisions
        self.index = 0

    def take(self, seat_username: str, seat_of: dict, kind: str) -> dict:
        if self.index >= len(self.decisions):
            raise ReplayMismatch(f"engine asked {seat_username} for a {kind} after the log ran out "
                                 f"({len(self.decisions)} decisions)")
        entry = self.decisions[self.index]
        expected_seat = seat_of.get(seat_username)
        if entry["seat"] != expected_seat or entry["kind"] != kind:
            raise ReplayMismatch(f"decision #{self.index}: engine asked seat {expected_seat} ({seat_username}) "
                                 f"for a {kind}, log has seat {entry['seat']} {entry['kind']}")
        self.index += 1
        return entry


def _make_replay_seat_class():
    from highsociety.code.gamecore.components_module.painting import Painting
    from highsociety.code.gamecore.player.player import BasePlayer

    class ReplaySeat(BasePlayer):
        """A seat that answers every prompt from the log instead of thinking."""

        def __init__(self, name: str, username: str, cursor: _Cursor, seat_of: dict):
            super().__init__(name, username)
            self.active = True
            self._cursor = cursor
            self._seat_of = seat_of

        def send_message(self, *args, **kwargs) -> None:
            pass

        def get_bid(self, timeout=None):
            return self._cursor.take(self.username, self._seat_of, "bid")["result"]

        def choose_painting_to_discard(self):
            entry = self._cursor.take(self.username, self._seat_of, "discard")
            if entry.get("active_after") is False:
                self.active = False
            value = entry["result"]
            if value is None:
                return None
            for card in self.status_cards:
                if isinstance(card, Painting) and card.value == value:
                    return card
            raise ReplayMismatch(f"{self.username} has no painting worth {value} to discard")

    return ReplaySeat


@contextlib.contextmanager
def _no_sleep():
    original = time.sleep
    time.sleep = lambda *a, **k: None
    try:
        yield
    finally:
        time.sleep = original


def replay_game(log: dict) -> dict:
    """
    Rebuilds the game `log` describes from nothing but the log, and raises
    ReplayMismatch at the first point the rebuilt game differs from it
    (deck, seat order, starting seat, which seat is asked to decide next,
    who won each auction for how much, final standings). Returns the
    rebuilt game's own log dict on success -- identical to `log` in every
    field this checks.
    """
    from highsociety.code.gamecore.game_manager import disgrace_settlement as settlements
    from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
    from highsociety.code.gamecore.game_manager.gameplay import PlayGame

    if log.get("version") != REPLAY_FORMAT_VERSION:
        raise ReplayUnsupported(f"unknown replay format version {log.get('version')!r}")
    if any(d["kind"] == "inactive" for d in log["decisions"]):
        raise ReplayUnsupported("a seat disconnected mid-game -- see game_replay.py's known limitation")

    seat_of = {username: i for i, username in enumerate(log["seats"])}
    cursor = _Cursor(log["decisions"])
    ReplaySeat = _make_replay_seat_class()
    players = [ReplaySeat(p["name"], p["username"], cursor, seat_of) for p in log["players"]]
    settlement = getattr(settlements, log["settlement"])()

    game = PlayGame(players=players, spectators=[], mode="network", seed=log["seed"],
                    turn_duration=None, disgrace_settlement=settlement,
                    auction_history=AuctionHistory(), pace_events=False)
    with _no_sleep():
        game.play_game()
    rebuilt = game.replay_log.to_dict()

    for field in ("deck", "seats", "starting_seat", "rounds", "result"):
        if rebuilt[field] != log[field]:
            raise ReplayMismatch(f"{field} differs:\n  logged:  {log[field]}\n  replayed: {rebuilt[field]}")
    if cursor.index != len(log["decisions"]):
        raise ReplayMismatch(f"game ended after {cursor.index} of {len(log['decisions'])} logged decisions")
    return rebuilt
