import random

# Playful names for bot players, deliberately not type-based ("Greedy1",
# "Capped2") — a bot should read like just another name at the table, not a
# labeled test fixture. The plan is to surface bot *difficulty*
# (Easy/Medium/Hard) separately from this display name later, rather than
# baking difficulty into the name at all — so this pool intentionally has no
# connection to bot type.
BOT_NAME_POOL = [
    "Niel", "Wagon", "Maran", "Reya", "Reeny", "Bramble", "Cricket", "Wisp",
    "Pip", "Juno", "Marbles", "Ziggy", "Pepper", "Otto", "Luna", "Scout",
    "Biscuit", "Nova", "Frankie", "Milo",
]


def assign_bot_names(count: int, taken: set = None) -> list[str]:
    """
    Returns `count` distinct names, skipping any already in `taken` (e.g.
    usernames already seated in the room this bot is joining) — shuffled so
    repeated games don't hand out names in the same order every time. Falls
    back to BotN for any name needed beyond the pool (minus whatever's
    taken); HSConfig.json caps a game at 5 players total, so this never
    actually happens — it's just a safety net against ever crashing on it.
    """
    taken = {t.lower() for t in (taken or set())}
    pool = [n for n in BOT_NAME_POOL if n.lower() not in taken]
    random.shuffle(pool)
    names = pool[:count]
    if len(names) < count:
        overflow_start = len(names) + 1
        names += [f"Bot{i}" for i in range(overflow_start, count + 1)]
    return names
