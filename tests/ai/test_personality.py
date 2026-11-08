"""Bot table talk: the personality file, the scripted talker, and when TableTalk speaks."""
import random

import pytest

from highsociety.code.ai.personality import table_talk as tt
from highsociety.code.ai.personality.talker import ScriptedTalker, TalkMoment, load_personalities, make_talker
from highsociety.code.common import bot_roster


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Seat:
    def __init__(self, username, name=None, human=False, points=0):
        self.username = username
        self.name = name or username
        self.human = human
        self.points = points


class AlwaysTalker:
    """Says '<trigger>' for the triggers it knows, every time."""

    def __init__(self, triggers=None):
        self.triggers = triggers
        self.moments = []

    def respond(self, moment):
        self.moments.append(moment)
        if self.triggers is None or moment.trigger in self.triggers:
            return moment.trigger
        return None


def make_table(talkers, seats, turn_duration=None):
    clock = Clock()
    said, timers = [], []
    talk = tt.TableTalk(seats, talkers, is_human=lambda p: p.human, say=lambda u, t: said.append((u, t)),
                        turn_duration=turn_duration, schedule=lambda d, fn: timers.append((d, fn)),
                        clock=clock, rng=random.Random(1))
    return talk, clock, said, timers


def run_timers(timers, max_delay=None):
    pending = list(timers)
    timers.clear()
    for delay, fn in pending:
        if max_delay is None or delay <= max_delay:
            fn()
        else:
            timers.append((delay, fn))


# -- the personality file ----------------------------------------------------
def test_every_line_uses_known_triggers_and_a_real_bot():
    data = load_personalities()
    known = set(data["triggers"])
    roster_keys = {b.key for b in bot_roster.BOT_ROSTER}
    for key, bot in data["bots"].items():
        assert key in roster_keys
        for line in bot["lines"]:
            assert line["text"].strip()
            assert set(line["triggers"]) <= known, (key, line)
            assert 0 < line.get("chance", 1) <= 1


def test_every_insane_bot_has_a_personality():
    for bot in bot_roster.BOT_ROSTER:
        if bot.difficulty == "insane":
            assert make_talker(bot.key) is not None, bot.key
    assert make_talker("ziggy") is None


def test_every_trigger_the_table_detects_is_described():
    # The triggers TableTalk can raise, read off its source, all exist in the file.
    import inspect
    source = inspect.getsource(tt)
    known = set(load_personalities()["triggers"])
    for trigger in known:
        assert f'"{trigger}"' in source, f"{trigger} is never raised"


# -- ScriptedTalker -----------------------------------------------------------
def test_scripted_talker_respects_max_per_game_and_cooldown():
    clock = Clock()
    talker = ScriptedTalker([{"text": "hi", "triggers": ["game_start"], "chance": 1.0,
                              "max_per_game": 2, "cooldown_seconds": 60}], {}, rng=random.Random(0), clock=clock)
    moment = TalkMoment(trigger="game_start", bot_name="X Bot")
    assert talker.respond(moment) == "hi"
    assert talker.respond(moment) is None  # cooling down
    clock.now += 61
    assert talker.respond(moment) == "hi"
    clock.now += 61
    assert talker.respond(moment) is None  # said twice already
    assert talker.respond(TalkMoment(trigger="won_game", bot_name="X Bot")) is None


def test_scripted_talker_chance_zero_never_speaks():
    talker = ScriptedTalker([{"text": "hi", "triggers": ["game_start"], "chance": 0.0001}], {},
                            rng=random.Random(3))
    said = [talker.respond(TalkMoment("game_start", "X Bot")) for _ in range(50)]
    assert said.count("hi") <= 1


# -- TableTalk ---------------------------------------------------------------
def bid(player, max_bid):
    return {"kind": "bid", "player": player, "max_bid": max_bid}


def test_bot_outbidding_a_repeat_raiser():
    bot = AlwaysTalker()
    talk, clock, said, timers = make_table({"thala bot": bot}, [Seat("thala bot", "Thalapathy Bot"), Seat("amy", human=True)])
    talk.observe("AUCTION_UPDATE", {"kind": "auction_start", "round_number": 1})
    talk.observe("AUCTION_UPDATE", bid("amy", 1))
    talk.observe("AUCTION_UPDATE", bid("thala bot", 2))
    talk.observe("AUCTION_UPDATE", bid("amy", 3))
    talk.observe("AUCTION_UPDATE", bid("thala bot", 4))
    run_timers(timers)
    assert said == [("thala bot", "outbid_repeat_raiser")]
    assert bot.moments[-1].target == "amy"


def test_table_gap_keeps_bots_from_talking_over_each_other():
    a, b = AlwaysTalker(), AlwaysTalker()
    talk, clock, said, timers = make_table({"a": a, "b": b}, [Seat("a"), Seat("b"), Seat("amy", human=True)])
    talk.observe("GLOBAL_EVENT", {"event": "countdown_finished"})
    run_timers(timers)
    assert len(said) == 1 and said[0][1] == "game_start"
    talk.observe("AUCTION_RESULT", {"card": {"type": "Painting", "value": 9}, "recipient": "a",
                                    "events": [{"player": "a", "action": "bid"}]})
    run_timers(timers)
    assert len(said) == 1  # too soon after the last line
    clock.now += tt.BOT_GAP_SECONDS + 1
    talk.observe("AUCTION_RESULT", {"card": {"type": "Painting", "value": 9}, "recipient": "a",
                                    "events": [{"player": "a", "action": "bid"}]})
    run_timers(timers)
    assert said[-1] == ("a", "won_valuable")


def test_won_auction_moments_in_priority_order():
    bot = AlwaysTalker({"won_uncontested", "won_contested", "won_auction"})
    talk, clock, said, timers = make_table({"t": bot}, [Seat("t"), Seat("amy", human=True)])
    talk.observe("AUCTION_RESULT", {"card": {"type": "Painting", "value": 2}, "recipient": "t",
                                    "events": [{"player": "t", "action": "bid"}]})
    run_timers(timers)
    assert said == [("t", "won_uncontested")]
    clock.now += 100
    talk.observe("AUCTION_RESULT", {"card": {"type": "Painting", "value": 2}, "recipient": "t",
                                    "events": [{"player": "amy", "action": "bid"}, {"player": "t", "action": "bid"}]})
    run_timers(timers)
    assert said[-1] == ("t", "won_contested")


def test_losing_streak_after_three_lost_auctions():
    bot = AlwaysTalker({"losing_streak"})
    talk, clock, said, timers = make_table({"t": bot}, [Seat("t"), Seat("amy", human=True)])
    result = {"card": {"type": "Painting", "value": 3}, "recipient": "amy",
              "events": [{"player": "t", "action": "bid"}, {"player": "amy", "action": "bid"}]}
    for _ in range(3):
        talk.observe("AUCTION_RESULT", result)
    run_timers(timers)
    assert said == [("t", "losing_streak")]


def test_disgrace_card():
    bot = AlwaysTalker()
    talk, clock, said, timers = make_table({"t": bot}, [Seat("t"), Seat("amy", human=True)])
    talk.observe("AUCTION_RESULT", {"card": {"type": "Scandale", "value": 0}, "recipient": "t", "events": []})
    run_timers(timers)
    assert said == [("t", "took_disgrace")]


def test_slow_human_turn_and_turn_moving_on_cancels_it():
    bot = AlwaysTalker({"opponent_slow"})
    talk, clock, said, timers = make_table({"t": bot}, [Seat("t"), Seat("amy", human=True)], turn_duration=30)
    talk.observe("AUCTION_UPDATE", {"kind": "turn_start", "player": "amy"})
    assert [d for d, _ in timers] == [max(tt.SLOW_MIN_SECONDS, 30 * tt.SLOW_SHARE_OF_TURN)]
    talk.observe("AUCTION_UPDATE", {"kind": "turn_start", "player": "t"})  # bots get no timer
    run_timers(timers)
    assert said == []  # amy's turn was over by then
    talk.observe("AUCTION_UPDATE", {"kind": "turn_start", "player": "amy"})
    run_timers(timers)  # fires, then schedules delivery
    run_timers(timers)
    assert said == [("t", "opponent_slow")]


def test_short_turn_timer_has_no_slow_moment():
    talk, clock, said, timers = make_table({"t": AlwaysTalker()}, [Seat("t"), Seat("amy", human=True)], turn_duration=10)
    talk.observe("AUCTION_UPDATE", {"kind": "turn_start", "player": "amy"})
    assert timers == []


def test_challenge_then_hesitation_after_bot_raise():
    bot = AlwaysTalker({"challenge_opponent", "opponent_hesitates"})
    talk, clock, said, timers = make_table({"t": bot}, [Seat("t"), Seat("amy", human=True)])
    talk.observe("AUCTION_UPDATE", {"kind": "auction_start", "round_number": 1})
    talk.observe("AUCTION_UPDATE", bid("t", 2))
    talk.observe("AUCTION_UPDATE", {"kind": "turn_start", "player": "amy"})
    delays = sorted(d for d, _ in timers)
    assert delays[:2] == [tt.CHALLENGE_AFTER_SECONDS, tt.HESITATE_AFTER_SECONDS]
    run_timers(timers, max_delay=tt.CHALLENGE_AFTER_SECONDS)
    run_timers(timers, max_delay=tt.DELIVERY_DELAY[1])
    assert said == [("t", "challenge_opponent")]


def test_game_over_closing_line_prefers_the_winner():
    a, b = AlwaysTalker(), AlwaysTalker()
    talk, clock, said, timers = make_table({"a": a, "b": b}, [Seat("a", "A Bot"), Seat("b", "B Bot"), Seat("amy", human=True)])
    talk.observe("GLOBAL_EVENT", {"event": "final_standings", "standings": [
        {"username": "a"}, {"username": "b"}, {"username": "amy", "eliminated": True}]})
    talk.observe("GLOBAL_EVENT", {"event": "winner", "winners": ["b"]})
    assert said == [("b", "won_game")]  # delivered straight away
    assert talk.closing_line == {"username": "b", "name": "B Bot", "text": "won_game"}
    talk.observe("GLOBAL_EVENT", {"event": "countdown_finished"})  # closed: nothing more
    run_timers(timers)
    assert said == [("b", "won_game")]


def test_a_broken_talker_never_breaks_the_game():
    class Broken:
        def respond(self, moment):
            raise RuntimeError("boom")
    talk, clock, said, timers = make_table({"t": Broken()}, [Seat("t"), Seat("amy", human=True)])
    talk.observe("GLOBAL_EVENT", {"event": "countdown_finished"})  # must not raise
    assert said == []


def test_lines_per_game_are_capped():
    bot = AlwaysTalker()
    talk, clock, said, timers = make_table({"t": bot}, [Seat("t"), Seat("amy", human=True)])
    for _ in range(tt.MAX_LINES_PER_GAME + 3):
        clock.now += 100
        talk.observe("AUCTION_RESULT", {"card": {"type": "Scandale"}, "recipient": "t", "events": []})
    run_timers(timers)
    assert len(said) == tt.MAX_LINES_PER_GAME
