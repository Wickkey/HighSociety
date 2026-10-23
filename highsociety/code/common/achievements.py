"""
The achievement catalog and per-game detection logic. Deliberately pure --
no DB, no Flask, no gamecore imports -- so it can be unit-tested with plain
dicts/lists and reused wherever (matches matchmaking.py's own reasoning).

Two kinds of achievement, handled in two different places on purpose:
- Per-game ones (everything except the win-count milestones below) are
  fully decidable from a single just-finished game's own data -- see
  detect_per_game_achievements.
- Career ones (win counts, games played, a win streak) are cumulative
  across a player's whole history, which only the database knows -- see
  game_history.py's record_finished_game, which checks WIN_COUNT_MILESTONES,
  GAMES_PLAYED_MILESTONES and WIN_STREAK against that player's totals after
  this game's own row is already inserted.

Every achievement needs data that only exists on the live game object
(PlayGame.auction_rounds is never persisted -- see PlayGame.get_auction_
history()'s own docstring), so detection has to happen at game-end, right
where web_server.py's _record_game_history already runs, before the room
is torn down.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str


ACHIEVEMENTS: list[Achievement] = [
    Achievement("first_win", "First Victory", "Win your first game."),
    Achievement("hat_trick", "Hat Trick", "Win 3 games."),
    Achievement("high_society_regular", "High Society Regular", "Win 5 games."),
    Achievement("old_money", "Old Money", "Win 10 games."),
    Achievement("giant_slayer", "Giant Slayer", "Win a game with a Hard bot at the table."),
    Achievement("sniper", "Sniper", "Win an auction that only ever had one bid -- yours."),
    Achievement("free_lunch", "Free Lunch", "Win an auction paying nothing."),
    Achievement("minimalist", "Minimalist", "Win an auction for the lowest-value Painting."),
    Achievement("full_set", "Full Set", "Collect every Prestige card in a single game."),
    Achievement("collector", "Collector", "Win at least one of every card type offered in a game."),
    Achievement("fearless", "Fearless", "Win a game without ever passing or folding on an auction."),
    Achievement("master_of_disgrace", "Master of Disgrace", "Win a disgrace auction."),
    Achievement("dynasty", "Dynasty", "Win 25 games."),
    Achievement("socialite", "Socialite", "Play 10 games."),
    Achievement("veteran", "Veteran", "Play 50 games."),
    Achievement("on_a_roll", "On a Roll", "Win 3 games in a row."),
    Achievement("landslide", "Landslide", "Win a game by 10 or more points."),
    Achievement("photo_finish", "Photo Finish", "Win a game by 1 point or less."),
    Achievement("clean_hands", "Clean Hands", "Win a game without taking a single disgrace card."),
    Achievement("masterpiece", "Masterpiece", "Win the 10-value Painting."),
    Achievement("high_roller", "High Roller", "Pay 20 or more for a single card."),
    Achievement("narrow_escape", "Narrow Escape", "Escape elimination with just 1 more money than the poorest player."),
    Achievement("good_company", "Good Company", "Win a game against another person."),
    Achievement("hard_times", "Hard Times", "Win a game with two or more Hard bots at the table."),
]

# username (cumulative win count) -> achievement id. Checked by
# game_history.record_finished_game, not by detect_per_game_achievements
# below -- kept here so the two stay next to each other and in sync with
# ACHIEVEMENTS above.
WIN_COUNT_MILESTONES: dict[int, str] = {
    1: "first_win",
    3: "hat_trick",
    5: "high_society_regular",
    10: "old_money",
    25: "dynasty",
}

# Same idea for games played (won or not), and for winning this many of your
# most recent games in a row.
GAMES_PLAYED_MILESTONES: dict[int, str] = {
    10: "socialite",
    50: "veteran",
}
WIN_STREAK: tuple[int, str] = (3, "on_a_roll")

_PRESTIGE_CARDS_FOR_FULL_SET = 3
_NON_BID_ACTIONS = {"pass", "fold", "quit"}
_DISGRACE_CARD_TYPES = {"FauxPas", "Passe", "Scandale"}
_TOP_PAINTING_VALUE = 10
_HIGH_ROLLER_PRICE = 20
_LANDSLIDE_MARGIN = 10
_PHOTO_FINISH_MARGIN = 1


def detect_per_game_achievements(
    final_standings: list[dict],
    winner_usernames: set,
    auction_rounds: list[dict],
    bot_mix: list,
) -> dict[str, set[str]]:
    """
    Returns {username: {achievement_id, ...}} for every achievement (other
    than the win-count milestones -- see the module docstring) earned in
    this one game. Includes bots' usernames too (harmless -- the caller,
    game_history.record_finished_game, only ever persists achievements for
    real Google-linked accounts, so a bot's entry here is simply never
    looked at).

    final_standings: PlayGame.final_standings.
    winner_usernames: {w.username for w in game.winners}.
    auction_rounds: PlayGame.get_auction_history().
    bot_mix: GameRoom.bot_mix, e.g. ["hard", "easy"].
    """
    unlocked: dict[str, set[str]] = {s["username"]: set() for s in final_standings}
    won_types: dict[str, set[str]] = {u: set() for u in unlocked}
    took_disgrace: dict[str, bool] = {u: False for u in unlocked}
    prestige_wins: dict[str, int] = {u: 0 for u in unlocked}
    ever_passed_or_folded: dict[str, bool] = {u: False for u in unlocked}
    offered_types = {a["card"]["type"] for a in auction_rounds}

    for auction in auction_rounds:
        for event in auction["events"]:
            if event["player"] in ever_passed_or_folded and event["action"] in _NON_BID_ACTIONS:
                ever_passed_or_folded[event["player"]] = True

        recipient = auction["recipient"]
        if recipient is None or recipient not in unlocked:
            continue

        card = auction["card"]
        won_types[recipient].add(card["type"])
        if card["type"] == "PrestigeCard":
            prestige_wins[recipient] += 1
        if card["type"] in _DISGRACE_CARD_TYPES:
            took_disgrace[recipient] = True

        if auction["auction_type"] == "disgrace":
            # A disgrace auction's winner always pays 0 by construction
            # (see AuctionRecord's own docstring) -- free_lunch is
            # deliberately about a *normal* auction, so this branch stops
            # here rather than falling through to that check below.
            unlocked[recipient].add("master_of_disgrace")
            continue

        bid_events = [e for e in auction["events"] if e["action"] == "bid"]
        if len(bid_events) == 1 and bid_events[0]["player"] == recipient:
            unlocked[recipient].add("sniper")
        if auction["money_spent"].get(recipient, 0) == 0:
            unlocked[recipient].add("free_lunch")
        if card["type"] == "Painting" and card["value"] == 1:
            unlocked[recipient].add("minimalist")
        if card["type"] == "Painting" and card["value"] == _TOP_PAINTING_VALUE:
            unlocked[recipient].add("masterpiece")
        if auction["money_spent"].get(recipient, 0) >= _HIGH_ROLLER_PRICE:
            unlocked[recipient].add("high_roller")

    for username in unlocked:
        if prestige_wins[username] >= _PRESTIGE_CARDS_FOR_FULL_SET:
            unlocked[username].add("full_set")
        if offered_types and won_types[username] >= offered_types:
            unlocked[username].add("collector")

    # Narrow escape: the money-elimination rule knocked out someone with
    # exactly 1 less than you. A tie for poorest eliminates nobody, so there
    # is never an eliminated player to compare against then.
    eliminated_money = {s["money_left"] for s in final_standings if s.get("eliminated")}
    for s in final_standings:
        if not s.get("eliminated") and s.get("active", True) and s["money_left"] - 1 in eliminated_money:
            unlocked[s["username"]].add("narrow_escape")

    # Winning margins are measured against the best of everyone else who
    # was still in contention (not eliminated, not quit) -- an eliminated
    # player's points never counted toward winning in the first place.
    contenders = {s["username"]: s["points"] for s in final_standings
                  if not s.get("eliminated") and s.get("active", True)}
    humans_at_table = len(final_standings) - len(bot_mix)

    for username in winner_usernames:
        if username not in unlocked:
            continue
        if "hard" in bot_mix:
            unlocked[username].add("giant_slayer")
        if bot_mix.count("hard") >= 2:
            unlocked[username].add("hard_times")
        if not ever_passed_or_folded[username]:
            unlocked[username].add("fearless")
        if not took_disgrace[username]:
            unlocked[username].add("clean_hands")
        if humans_at_table >= 2:
            unlocked[username].add("good_company")
        others = [pts for u, pts in contenders.items() if u != username]
        if username in contenders and others:
            margin = contenders[username] - max(others)
            if margin >= _LANDSLIDE_MARGIN:
                unlocked[username].add("landslide")
            if margin <= _PHOTO_FINISH_MARGIN:
                unlocked[username].add("photo_finish")

    return {u: ids for u, ids in unlocked.items() if ids}
