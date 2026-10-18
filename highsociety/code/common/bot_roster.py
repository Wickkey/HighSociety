"""
The named bots -- each one a persistent identity with a fixed difficulty.

"Ziggy bot" is always an Easy bot, in every game, so it has one players row
(see game_history.py's `bots` table), one Elo, one stats history and one
public profile -- and two Easy bots at the same table are still two
different players. A seat of difficulty X is only ever filled by one of X's
names (see ai/bot_names.py's assign_bot_names).

Lives in common/ rather than ai/ so game_history.py (a plain persistence
layer) can seed the database from it without importing the bot engine.

Sizing: a table seats at most 5, so at most 4 bots of one difficulty ever
sit together (one seat is always the human host) -- every tier here has
at least 6 names, so a tier can't run dry from seating alone.
"""
from typing import NamedTuple, Optional

DIFFICULTIES = ("easy", "medium", "hard")


class RosterBot(NamedTuple):
    key: str          # stable id, never shown: players.username is f"__bot_{key}__"
    name: str         # the in-game seat name, e.g. "Ziggy bot"
    difficulty: str   # "easy" / "medium" / "hard"
    bio: str


def _bot(name: str, difficulty: str, bio: str) -> RosterBot:
    return RosterBot(key=name.lower(), name=f"{name} bot", difficulty=difficulty, bio=bio)


BOT_ROSTER = (
    # -- Easy ---------------------------------------------------------------
    _bot("Ziggy", "easy", "Bids on whatever's shiny. Usually regrets it about two rounds later."),
    _bot("Pip", "easy", "Small, cheerful, and convinced every Painting is a bargain."),
    _bot("Biscuit", "easy", "Plays for the joy of it. Keeps the big money cards out of sentimentality."),
    _bot("Marbles", "easy", "Has lost a few. Keeps losing more, but always with a smile."),
    _bot("Cricket", "easy", "Chirps a bid, hops out, chirps another. Rarely counts what's left."),
    _bot("Wisp", "easy", "Drifts in and out of auctions like a breeze. Hard to pin down, easy to beat."),
    _bot("Pepper", "easy", "A little spicy on the opening bid, a lot less spicy after that."),
    # -- Medium -------------------------------------------------------------
    _bot("Milo", "medium", "Watches who's running low on cash and waits for them to blink."),
    _bot("Juno", "medium", "Plans three auctions ahead, occasionally forgets the fourth."),
    _bot("Otto", "medium", "Methodical, polite, and quietly keeping a list of your bids."),
    _bot("Scout", "medium", "Probes every auction with a cheap bid just to see who flinches."),
    _bot("Reeny", "medium", "Never the richest at the table, rarely the poorest. That's the point."),
    _bot("Wagon", "medium", "Slow to start, hard to stop once the good Paintings come out."),
    _bot("Bramble", "medium", "Prickly about disgrace cards. Will pay real money to dodge one."),
    # -- Hard ---------------------------------------------------------------
    _bot("Frankie", "hard", "Knows the deck, knows your money, knows you're bluffing."),
    _bot("Niel", "hard", "Spends exactly enough. Not a franc more."),
    _bot("Maran", "hard", "Patient as a glacier. Wins the auctions that matter and lets the rest go."),
    _bot("Reya", "hard", "Reads the table like a ledger and balances it in her favor."),
    _bot("Luna", "hard", "Comes alive in the endgame, when everyone else has run out of money."),
    _bot("Nova", "hard", "Ruthless with the Prestige cards. Leaves the scraps for you."),
)

_BY_NAME = {b.name.lower(): b for b in BOT_ROSTER}
_BY_USERNAME = {f"__bot_{b.key}__": b for b in BOT_ROSTER}


def roster_username(bot: RosterBot) -> str:
    """The bot's players.username -- namespaced so it can never collide with
    a real guest/Google username (see guest_username.py, which never
    produces a "__" prefix), same convention as the legacy __bot_easy__."""
    return f"__bot_{bot.key}__"


def names_for_difficulty(difficulty: str) -> list[str]:
    return [b.name for b in BOT_ROSTER if b.difficulty == difficulty]


def find_by_name(name: Optional[str]) -> Optional[RosterBot]:
    """The roster entry behind an in-game seat name ("Ziggy bot"), or None
    for anything else (a human, an overflow "Bot3", a renamed bot)."""
    return _BY_NAME.get((name or "").lower())


def find_by_username(username: Optional[str]) -> Optional[RosterBot]:
    return _BY_USERNAME.get(username or "")
