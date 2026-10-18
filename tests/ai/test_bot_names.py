import pytest

from highsociety.code.ai import create_bot_players
from highsociety.code.ai.bot_names import assign_bot_names
from highsociety.code.common import bot_roster


@pytest.mark.parametrize("difficulty", bot_roster.DIFFICULTIES)
def test_a_difficulty_seat_only_ever_gets_one_of_its_own_names(difficulty):
    allowed = set(bot_roster.names_for_difficulty(difficulty))
    for _ in range(50):
        names = assign_bot_names(4, difficulties=[difficulty] * 4)  # the most one table can ever seat
        assert len(set(names)) == 4
        assert set(names) <= allowed


def test_every_tier_can_fill_the_biggest_possible_table():
    for difficulty in bot_roster.DIFFICULTIES:
        assert len(bot_roster.names_for_difficulty(difficulty)) >= 4


def test_roster_names_and_keys_are_unique():
    names = [b.name.lower() for b in bot_roster.BOT_ROSTER]
    keys = [b.key for b in bot_roster.BOT_ROSTER]
    assert len(set(names)) == len(names) and len(set(keys)) == len(keys)
    assert all(b.difficulty in bot_roster.DIFFICULTIES for b in bot_roster.BOT_ROSTER)


def test_a_mixed_table_gets_matching_names_and_no_duplicates():
    for _ in range(50):
        bots = create_bot_players(["hard", "easy", "easy", "medium"], think_time=0)
        assert len({b.username for b in bots}) == 4
        for bot in bots:
            assert bot_roster.find_by_name(bot.name).difficulty == bot.difficulty


def test_names_already_seated_are_skipped():
    easy = bot_roster.names_for_difficulty("easy")
    taken = {n.lower() for n in easy[:-1]}  # leave exactly one easy name free
    assert assign_bot_names(1, taken=taken, difficulties=["easy"]) == [easy[-1]]


def test_an_exhausted_tier_falls_back_instead_of_borrowing_another_tiers_name():
    taken = {n.lower() for n in bot_roster.names_for_difficulty("hard")}
    assert assign_bot_names(1, taken=taken, difficulties=["hard"]) == ["Bot1"]


def test_heuristic_bots_still_draw_from_the_whole_pool():
    all_names = {b.name for b in bot_roster.BOT_ROSTER}
    names = assign_bot_names(3, difficulties=["greedy", None, "pass"])
    assert len(set(names)) == 3 and set(names) <= all_names


def test_mismatched_difficulties_length_is_rejected():
    with pytest.raises(ValueError):
        assign_bot_names(2, difficulties=["easy"])
