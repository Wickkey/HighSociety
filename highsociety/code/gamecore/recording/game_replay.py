"""
Rebuilds a finished game from its recorded actions and checks that it plays
out identically.

The source of truth is the action log (PlayGame.action_log, stored one row
per action in game_history.py's `game_actions` table) plus the game's seed
(`games.seed`). The seed fixes everything random -- deck order and the
starting player -- and the actions supply every decision, in order. Nothing
else is needed: a bot's own thinking is never re-run, only its recorded
choices are fed back.

A game record (game_record() below; the same shape fetch_game_record in
game_history.py assembles from the database, and the shape selfplay_games
stores for RL) is:

    {
      "version": 1,
      "seed": 123,
      "green_card_limit": 4,
      "settlement": "ForfeitSettlement",
      "seats": [{"seat": 0, "username", "name", "kind"}, ...],   # turn order
      "actions": [                                              # = game_actions rows
        {"seq": 1, "round": 1, "seat": 2, "action": "bid", "amount": 3,
         "cards": [1, 2], "think_ms": 1840, "reason": null, "timestamp": "..."},
        ...
      ],
      "rounds": [{"round", "card": {"type", "value"}, "recipient_seat", "price"}],
      "standings": [{"seat", "points", "money_left", "is_winner", "eliminated"}]
    }

Actions:
  bid       amount = new total, cards = money cards committed so far this auction
  pass/fold a choice, or forced: reason "timeout" (turn clock ran out) or
            "disconnected" (the seat had dropped by its turn)
  quit      likewise; a disgrace-auction quit can be forced ("disconnected")
  discard   Faux Pas penalty; amount = the discarded painting's value
  disconnect / resign / reconnect
            a change in the seat's connection; amount = the engine's liveness
            check number at which it was seen (see PlayGame._sync_liveness),
            which is what lets the replay drop the seat at the same moment
"""
import contextlib
import time
from collections import Counter
from typing import Optional

GAME_RECORD_VERSION = 1

LIVENESS_ACTIONS = ("disconnect", "resign", "reconnect")
# The per-action fields that must replay identically. think_ms and
# timestamp are wall-clock facts about the original game, not outcomes.
_COMPARED_FIELDS = ("seq", "round", "seat", "action", "amount", "cards", "reason")


class ReplayMismatch(Exception):
    """The rebuilt game diverged from the recorded one."""


class ReplayUnsupported(Exception):
    """The record can't be replayed (e.g. a game from before actions carried seats)."""


def _player_kind(player) -> str:
    from highsociety.code.ai import BOT_TYPES
    for kind, cls in BOT_TYPES.items():
        if type(player) is cls:
            return kind
    if getattr(player, "is_human", False) or type(player).__name__ in ("NetworkPlayer", "CLIPlayer"):
        return "human"
    return type(player).__name__


def game_record(game) -> dict:
    """The replayable record of a finished PlayGame (see module docstring)."""
    seat_of = {p.username: i for i, p in enumerate(game.players)}
    winners = {w.username for w in (game.winners or [])}
    return {
        "version": GAME_RECORD_VERSION,
        "seed": game.seed,
        "green_card_limit": game.green_card_limit,
        "settlement": type(game.disgrace_settlement).__name__,
        "seats": [{"seat": i, "username": p.username, "name": p.name, "kind": _player_kind(p)}
                  for i, p in enumerate(game.players)],
        "actions": [{k: v for k, v in row.items() if k != "username"} for row in game.action_log],
        "rounds": [
            {
                "round": r.round_number,
                "card": {"type": r.card["type"], "value": r.card["value"]},
                "recipient_seat": seat_of.get(r.recipient) if r.recipient else None,
                "price": (r.money_spent.get(r.recipient) or 0) if r.recipient and r.auction_type == "normal" else None,
            }
            for r in game.auction_rounds
        ],
        "standings": [
            {"seat": seat_of[s["username"]], "points": s["points"], "money_left": s["money_left"],
             "is_winner": s["username"] in winners, "eliminated": s["eliminated"]}
            for s in game.final_standings
        ],
    }


def _make_replay_seat_class():
    from highsociety.code.gamecore.components_module.painting import Painting
    from highsociety.code.gamecore.player.player import BasePlayer

    class ReplaySeat(BasePlayer):
        """A seat that answers every prompt from its recorded actions."""

        def __init__(self, seat: int, name: str, username: str, actions: list[dict]):
            super().__init__(name, username)
            self.active = True
            self.seat = seat
            mine = [a for a in actions if a["seat"] == seat]
            # Only the actions the engine asks this seat for. A forced pass
            # or quit ("disconnected") the engine produces itself, without
            # asking -- and a timeout is answered as the pass it became.
            self._decisions = [a for a in mine if a["action"] not in LIVENESS_ACTIONS
                               and a.get("reason") != "disconnected"]
            self._liveness = [a for a in mine if a["action"] in LIVENESS_ACTIONS]
            self.decisions_used = 0

        def send_message(self, *args, **kwargs) -> None:
            pass

        def replay_liveness(self, check: int) -> bool:
            """Connected as of liveness check `check`: every recorded change
            for this seat seen at or before that check, applied in order."""
            live = True
            for change in self._liveness:
                if change["amount"] <= check:
                    live = change["action"] == "reconnect"
            return live

        def _next(self, wanted: tuple) -> Optional[dict]:
            if self.decisions_used >= len(self._decisions):
                raise ReplayMismatch(f"seat {self.seat} was asked to act after its last recorded action")
            action = self._decisions[self.decisions_used]
            if action["action"] not in wanted:
                return None
            self.decisions_used += 1
            return action

        def get_bid(self, timeout=None):
            action = self._next(("bid", "pass", "fold", "quit"))
            if action is None:
                raise ReplayMismatch(f"seat {self.seat} was asked to bid, but its next recorded action is "
                                     f"{self._decisions[self.decisions_used]}")
            if action["action"] != "bid":
                return action["action"]
            # The record holds every card committed so far this auction; the
            # engine wants only the ones added on this turn.
            added = Counter(action["cards"]) - Counter(c.value for c in self.current_money_card_bids)
            return sorted(added.elements())

        def choose_painting_to_discard(self):
            action = self._next(("discard",))
            if action is None:
                # The original seat never answered this prompt (it dropped
                # while choosing); its disconnect follows in the record.
                return None
            for card in self.status_cards:
                if isinstance(card, Painting) and card.value == action["amount"]:
                    return card
            raise ReplayMismatch(f"seat {self.seat} has no painting worth {action['amount']} to discard")

    return ReplaySeat


@contextlib.contextmanager
def _no_sleep():
    # Bots pace themselves with time.sleep; replay seats don't, but the
    # engine's own pacing still might -- a replay should never wait.
    original = time.sleep
    time.sleep = lambda *a, **k: None
    try:
        yield
    finally:
        time.sleep = original


def _comparable(actions: list[dict]) -> list[tuple]:
    rows = []
    for a in actions:
        if a["action"] in LIVENESS_ACTIONS:
            continue  # inputs, not outcomes -- see replay_record
        reason = None if a.get("reason") == "timeout" else a.get("reason")
        rows.append(tuple(reason if f == "reason" else (list(a[f]) if f == "cards" and a[f] is not None else a[f])
                          for f in _COMPARED_FIELDS if f != "seq"))
    return rows


def replay_record(record: dict) -> dict:
    """
    Rebuilds the game `record` describes from nothing but its seed and
    actions, and raises ReplayMismatch at the first difference: any bid,
    pass, quit or discard (who, when, how much, which cards), any round's
    card, recipient or price, or any seat's final standing. Returns the
    rebuilt game's own record.

    Disconnect/reconnect rows are inputs (they drive when a replay seat
    drops), so they're checked by their consequences -- the forced passes
    and quits they cause -- rather than compared row by row. A timeout is
    replayed as the pass it became.
    """
    from highsociety.code.gamecore.game_manager import disgrace_settlement as settlements
    from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
    from highsociety.code.gamecore.game_manager.gameplay import PlayGame

    if record.get("version") != GAME_RECORD_VERSION:
        raise ReplayUnsupported(f"unknown game record version {record.get('version')!r}")
    if record.get("seed") is None:
        raise ReplayUnsupported("no seed recorded (a game from before games.seed existed)")
    if any(a.get("seat") is None for a in record["actions"]):
        raise ReplayUnsupported("actions without a seat (a game from before game_actions.seat existed)")

    ReplaySeat = _make_replay_seat_class()
    seats = [ReplaySeat(s["seat"], s["name"], s["username"], record["actions"])
             for s in sorted(record["seats"], key=lambda s: s["seat"])]
    settlement = getattr(settlements, record.get("settlement") or "ForfeitSettlement")()
    game = PlayGame(players=list(seats), spectators=[], mode="network", seed=record["seed"],
                    turn_duration=None, disgrace_settlement=settlement,
                    auction_history=AuctionHistory(), pace_events=False, preseated=True)
    if game.green_card_limit != record.get("green_card_limit", game.green_card_limit):
        raise ReplayUnsupported(f"recorded with green_card_limit={record['green_card_limit']}, "
                                f"engine now uses {game.green_card_limit}")
    with _no_sleep():
        game.play_game()
    rebuilt = game_record(game)

    logged, replayed = _comparable(record["actions"]), _comparable(rebuilt["actions"])
    for i, (a, b) in enumerate(zip(logged, replayed)):
        if a != b:
            raise ReplayMismatch(f"action #{i + 1} differs (round, seat, action, amount, cards, reason):\n"
                                 f"  recorded: {a}\n  replayed: {b}")
    if len(logged) != len(replayed):
        raise ReplayMismatch(f"recorded {len(logged)} actions, replay produced {len(replayed)}")
    for field in ("rounds", "standings"):
        want = sorted(record[field], key=lambda r: r.get("round", r.get("seat")))
        got = sorted(rebuilt[field], key=lambda r: r.get("round", r.get("seat")))
        if want != got:
            raise ReplayMismatch(f"{field} differ:\n  recorded: {want}\n  replayed: {got}")
    unused = [s.seat for s in seats if s.decisions_used != len(s._decisions)]
    if unused:
        raise ReplayMismatch(f"seats {unused} have recorded actions the replay never asked for")
    return rebuilt
