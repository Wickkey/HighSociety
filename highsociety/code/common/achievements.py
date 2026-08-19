"""
The achievement catalog and per-game detection logic. Deliberately pure --
no DB, no Flask, no gamecore imports -- so it can be unit-tested with plain
dicts/lists and reused wherever (matches matchmaking.py's own reasoning).

Two kinds of achievement, handled in two different places on purpose:
- Per-game ones (everything except the win-count milestones below) are
  fully decidable from a single just-finished game's own data -- see
  detect_per_game_achievements.
- Win-count milestones (first_win/hat_trick/...) are cumulative across a
  player's whole history, which only the database knows -- see
  game_history.py's record_finished_game, which checks WIN_COUNT_MILESTONES
  against that player's total win count after this game's own row is
  already inserted.

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
}

_PRESTIGE_CARDS_FOR_FULL_SET = 3
_NON_BID_ACTIONS = {"pass", "fold", "quit"}


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

    for username in unlocked:
        if prestige_wins[username] >= _PRESTIGE_CARDS_FOR_FULL_SET:
            unlocked[username].add("full_set")
        if offered_types and won_types[username] >= offered_types:
            unlocked[username].add("collector")

    for username in winner_usernames:
        if username not in unlocked:
            continue
        if "hard" in bot_mix:
            unlocked[username].add("giant_slayer")
        if not ever_passed_or_folded[username]:
            unlocked[username].add("fearless")

    return {u: ids for u, ids in unlocked.items() if ids}
