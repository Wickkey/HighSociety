from main import create_bot_players, create_players
from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.ai.bot_names import BOT_NAME_POOL


def test_create_bot_players_builds_one_instance_per_entry_with_distinct_usernames():
    players = create_bot_players(["greedy", "greedy", "pass"])

    assert [type(p) for p in players] == [GreedyBot, GreedyBot, PassBot]
    # Names are randomly assigned (see bot_names.py) rather than a
    # type+number pattern like "greedy1" — just pin down the properties
    # that actually matter: one distinct name per bot, drawn from the real
    # pool, with username/name consistently paired (lowercase/capitalized).
    usernames = [p.username for p in players]
    assert len(set(usernames)) == 3
    for p in players:
        assert p.name.lower() == p.username
        assert p.name.endswith(" bot")
        assert p.name[: -len(" bot")] in BOT_NAME_POOL


def test_create_players_fills_bot_seats_first_and_prompts_for_the_rest(monkeypatch):
    """--bots greedy fills seat 1 without prompting; seats 2-3 fall through
    to the normal interactive CLIPlayer setup."""
    prompted_indices = []

    def fake_get_player_details(player_idx):
        prompted_indices.append(player_idx)
        return f"human{player_idx}", f"Human {player_idx}"

    monkeypatch.setattr("main.get_player_details", fake_get_player_details)

    players = create_players(num_players=3, bot_mix=["greedy"])

    assert type(players[0]) is GreedyBot
    assert players[0].name.endswith(" bot")
    assert players[0].name[: -len(" bot")] in BOT_NAME_POOL
    assert [p.username for p in players[1:]] == ["human1", "human2"]
    assert prompted_indices == [1, 2]


def test_create_players_with_no_bots_prompts_for_every_seat(monkeypatch):
    monkeypatch.setattr("main.get_player_details", lambda i: (f"human{i}", f"Human {i}"))

    players = create_players(num_players=2)

    assert len(players) == 2
