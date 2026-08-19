"""
Multiplayer Elo rating updates. A card game's result is a full ranking of
N players (see PlayGame.final_standings), not a single winner/loser pair
-- the standard two-player Elo formula doesn't directly apply. This uses
the common "pairwise" generalization: for every pair of players in the
game, treat the outcome as if they'd played a 1v1 (whoever scored more
points "won" that pair, equal points is a draw), compute the usual Elo
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
    standings: one dict per rated player -- {"username", "points", "rating"}
    (their Elo *before* this game). Only ever called with players whose
    rating actually means something persistent (see game_history.py's own
    call site, which filters to Google-linked accounts before this --
    same reasoning as achievements.py's own gating: a guest's identity
    resets on every browser clear, so there's nothing meaningful to track
    a rating against). Bots are never included here at all.

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
            if a["points"] > b["points"]:
                score_a, score_b = 1.0, 0.0
            elif a["points"] < b["points"]:
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
