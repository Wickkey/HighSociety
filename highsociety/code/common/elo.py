"""
Multiplayer Elo rating updates. A card game's result is a full ranking of
N players (see PlayGame.final_standings), not a single winner/loser pair
-- the standard two-player Elo formula doesn't directly apply. This uses
the common "pairwise" generalization: for every pair of players in the
game, treat the outcome as if they'd played a 1v1 (whoever placed better
"won" that pair, an equal placement is a draw), compute the usual Elo
expected-score delta for that pair, and average each player's deltas
across every pair they're part of -- averaging (not summing) keeps a
game's total rating swing comparable across table sizes, since a 5-player
game has 4 pairs per player where a 2-player game only has 1.

Deliberately its own small, swappable function rather than folded into
game_history.py's SQL -- the rating math itself (K-factor, this pairwise
approach vs. something like a full multiplayer Elo-MMR/TrueSkill model)
is the part most likely to change later. Anyone revisiting this only
needs to change compute_elo_deltas' body; nothing about how ratings get
read from or written back to the database needs to know the formula
changed.
"""

DEFAULT_K_FACTOR = 32


def compute_elo_deltas(standings: list, k_factor: float = DEFAULT_K_FACTOR) -> dict:
    """
    standings: one dict per rated player -- {"username", "placement",
    "rating"} (their Elo *before* this game). `placement` is this
    player's final rank (1 = actual game winner, matching
    game_history.py's own placement_by_index) -- NOT raw points: this
    game's real winner is whoever has the most points *among players not
    eliminated* for having the least money (see PlayGame's own win
    condition), so the points leader can genuinely finish last. A real
    bug, confirmed live: rating deltas used to compare raw points
    directly, so an eliminated player with the game's highest score still
    gained Elo despite finishing dead last by the game's own rules --
    exactly the "why did my rating go up when I lost" case placement
    (unlike points) already accounts for correctly.

    Only ever called with participants whose rating actually means
    something persistent: a Google-linked human (a guest's identity
    resets on every browser clear, so there's nothing meaningful to
    track a rating against -- same reasoning as achievements.py's own
    gating), or a bot with a known difficulty (see game_history.py's
    `bots` table -- each difficulty tier has one shared, real, evolving
    rating of its own, used here exactly like a human's so a human's
    rating actually moves in practice against the overwhelmingly common
    solo-vs-bots case, but never surfaced to players anywhere in the UI).
    "username" here is whatever key the caller used to identify each
    participant -- not necessarily a real players.username, see
    game_history.py's `game_username` for why a bot needs a different one.

    Returns {username: delta} (whole numbers, can be negative). Fewer
    than 2 rated players means there's no comparison to make -- returns
    all zeros rather than raising, so the caller doesn't need a separate
    guard for "everyone else at the table was a bot."
    """
    if len(standings) < 2:
        return {s["username"]: 0 for s in standings}

    deltas = {s["username"]: 0.0 for s in standings}
    pairs_played = {s["username"]: 0 for s in standings}

    for i in range(len(standings)):
        for j in range(i + 1, len(standings)):
            a, b = standings[i], standings[j]
            # Lower placement number is the better finish (1st beats 2nd).
            if a["placement"] < b["placement"]:
                score_a, score_b = 1.0, 0.0
            elif a["placement"] > b["placement"]:
                score_a, score_b = 0.0, 1.0
            else:
                score_a = score_b = 0.5

            expected_a = 1 / (1 + 10 ** ((b["rating"] - a["rating"]) / 400))
            expected_b = 1 - expected_a

            deltas[a["username"]] += k_factor * (score_a - expected_a)
            deltas[b["username"]] += k_factor * (score_b - expected_b)
            pairs_played[a["username"]] += 1
            pairs_played[b["username"]] += 1

    return {username: round(deltas[username] / pairs_played[username]) for username in deltas}
