import random
from typing import Optional, Sequence

from highsociety.code.common.bot_roster import BOT_ROSTER, DIFFICULTIES, find_by_name, names_for_difficulty

# Playful names for bot players, deliberately not type-based ("Greedy1",
# "Capped2") — a bot should read like just another name at the table, not a
# labeled test fixture. Each name is a persistent identity with a fixed
# difficulty (see common/bot_roster.py): "Ziggy bot" is always Easy, so it
# has its own profile, stats and Elo across every game it plays.
BOT_NAME_POOL = [b.name[: -len(" Bot")] for b in BOT_ROSTER]


def assign_bot_names(count: int, taken: set = None,
                     difficulties: Optional[Sequence[Optional[str]]] = None,
                     pinned: Optional[Sequence[str]] = None) -> list[str]:
    """
    Returns `count` distinct " Bot"-suffixed names (e.g. "Niel Bot"), so a
    bot reads as clearly a bot at a glance without resorting to a type-based
    label ("Greedy1") — skips any already in `taken` (e.g. usernames already
    seated in the room this bot is joining, compared against the same
    suffixed form so an add-a-bot-later call can't collide with a bot seated
    at creation time), shuffled so repeated games don't hand out names in
    the same order every time.

    difficulties: one entry per name wanted. A slot whose entry is
    "easy"/"medium"/"hard" only ever gets one of that difficulty's own
    roster names — that's what makes a name a stable identity (see
    common/bot_roster.py). Any other entry (None, or a heuristic bot type
    like "greedy" that has no difficulty) draws from the whole pool, same as
    omitting `difficulties` entirely. Those bots are never recorded under a
    roster identity (see game_history.record_finished_game), so the name is
    just a label for them.

    pinned: roster names ("Thanos Bot") that must be seated if they can be:
    each takes the first slot of its own difficulty (a challenge game, see
    web_server.py, always seats the bot it names). One that has no such slot,
    or is already taken, is skipped.

    Falls back to BotN for any slot its pool can't fill. Every difficulty
    has at least 5 names and a table seats at most 4 bots, so this never
    actually happens — it's just a safety net against ever crashing on it.
    """
    taken = {t.lower() for t in (taken or set())}
    slots = list(difficulties) if difficulties is not None else [None] * count
    if len(slots) != count:
        raise ValueError(f"difficulties has {len(slots)} entries for {count} names")

    def _available(pool):
        names = [n for n in pool if n.lower() not in taken]
        random.shuffle(names)
        return names

    names: list[Optional[str]] = [None] * count
    for name in pinned or ():
        bot = find_by_name(name)
        if bot is None or bot.name.lower() in taken:
            continue
        slot = next((i for i in range(count) if names[i] is None and slots[i] == bot.difficulty), None)
        if slot is not None:
            names[slot] = bot.name
            taken.add(bot.name.lower())
    # Difficulty slots first: they can only use their own tier's names, so a
    # free-for-all slot must not grab one of those first.
    order = sorted(range(count), key=lambda i: slots[i] not in DIFFICULTIES)
    for i in order:
        if names[i] is not None:
            continue
        difficulty = slots[i]
        pool = names_for_difficulty(difficulty) if difficulty in DIFFICULTIES else [b.name for b in BOT_ROSTER]
        candidates = _available(pool)
        if candidates:
            names[i] = candidates[0]
            taken.add(candidates[0].lower())

    overflow = 1
    for i in range(count):
        if names[i] is None:
            while f"bot{overflow}" in taken:
                overflow += 1
            names[i] = f"Bot{overflow}"
            taken.add(names[i].lower())
    return names
