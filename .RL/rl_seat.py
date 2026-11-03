"""
Connects the (27, 6) state encoding to the real game engine.

    RLSeat      a player seat the engine drives like any bot. On each decision
                it encodes the live game into the (27, 6) state, asks a policy
                for an output vector, and decodes that back into an engine move.
    play_game   sets up one game: an RLSeat plus engine bots, plays it, and
                returns the game record and every decision the seat made.

Output layer (bids) -- 12 entries:
    0..10  money cards 1 2 3 4 6 8 10 12 15 20 25: 1 = put this card down now
           (on top of whatever is already on the table this auction)
    11     pass

A policy is any object with:
    bid(state, mask)     -> length-12 0/1 vector   (mask: 1 = card in hand / pass)

The Faux Pas discard is not a policy decision: the seat always discards its
lowest painting. Score only grows with the total painting value, so that is
always optimal (and it's what every bot did in all 7,659 rl-v1 discards).
"""
import contextlib
import random
import sys
import time
from itertools import combinations
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # repo root

from highsociety.code.gamecore.player.player import BasePlayer          # noqa: E402
from highsociety.code.gamecore.components_module.painting import Painting  # noqa: E402

import state_encoding as se                                            # noqa: E402

N_BID_OUT = se.N_MONEY + 1          # 12
PASS = se.N_MONEY                   # index 11


# ---------------------------------------------------------------- live encoding

def _row(card, prestige_slot):
    t, v = card["type"], card["value"]
    if t == "Painting":
        return se.PAINT0 + v - 1
    if t == "PrestigeCard":
        return se.PRESTIGE0 + prestige_slot
    return {"FauxPas": se.FAUX_PAS, "Passe": se.PASSE, "Scandale": se.SCANDALE}[t]


def encode_live(players, history, live_card, me, perspective=True):
    """
    The (27, 6) state of a game in progress, built from the engine's own
    objects: `players` (PlayGame.players, turn order), `history` (completed
    AuctionRecords, oldest first), `live_card` (card dict on auction now, or
    None). Produces exactly what state_encoding.encode_game produces from the
    finished game's record at the same decision.
    """
    g = se._Game({"seats": players, "settlement": None})
    seat_of = {p.username: i for i, p in enumerate(players)}

    prestige = 0
    for rec in history:                          # cards already auctioned
        r = _row(rec.card, prestige)
        prestige += rec.card["type"] == "PrestigeCard"
        g.done.add(r)
        if rec.recipient is not None and rec.card["type"] == "PrestigeCard":
            g.owned[seat_of[rec.recipient]].add(r)

    for i, p in enumerate(players):
        g.hand[i] = {c.value for c in p.money_cards}
        g.table[i] = {c.value for c in p.current_money_card_bids}
        for c in p.status_cards:                  # prestige handled above (needs slot order)
            if type(c).__name__ != "PrestigeCard":
                g.owned[i].add(_row({"type": type(c).__name__, "value": c.value}, 0))

    if live_card is not None:
        g.current = _row(live_card, prestige)
        g.in_auction = [bool(p.current_participation_in_auction) for p in players]
        top = max(p.current_bid_value for p in players)
        g.high_bidder = next((i for i, p in enumerate(players) if top and p.current_bid_value == top), None)
    return g.encode(me, perspective=perspective)


# ---------------------------------------------------------------- actions

def bid_mask(state):
    """1 for every money card in my hand (col 0 == 1), and for pass."""
    m = np.zeros(N_BID_OUT, dtype=np.int8)
    m[:se.N_MONEY] = state[:se.N_MONEY, 0] == 1
    m[PASS] = 1
    return m


def action_to_vector(state, action):
    """A recorded bid/pass action row -> the output vector it corresponds to
    (for training targets from the self-play data)."""
    v = np.zeros(N_BID_OUT, dtype=np.int8)
    if action["action"] == "bid":
        on_table = {se.MONEY[i] for i in range(se.N_MONEY) if state[i, 0] == -1}
        for c in set(action["cards"]) - on_table:
            v[se.MONEY_ROW[c]] = 1
    else:
        v[PASS] = 1
    return v


# ---------------------------------------------------------------- candidate bids (afterstates)
#
# Instead of one output per card, a policy can list every legal bid, write each
# one into the state ("afterstate": the table as it would look after that bid)
# and score those states. A candidate is an index into SUBSETS -- the set of
# money cards it ADDS this turn; index 0 is the empty set, i.e. pass.

MAX_BID_CARDS = 5          # never more than this many cards on the table in one auction
_MONEY = np.array(se.MONEY)

SUBSETS = np.array([[i in c for i in range(se.N_MONEY)]
                    for n in range(MAX_BID_CARDS + 1)
                    for c in combinations(range(se.N_MONEY), n)], dtype=np.int8)   # (1024, 11)
SUBSET_SIZE = SUBSETS.sum(1)
SUBSET_VALUE = SUBSETS.astype(int) @ _MONEY
_SUBSET_INDEX = {SUBSETS[i].tobytes(): i for i in range(len(SUBSETS))}
PASS_CAND = 0


def bid_totals(state):
    """(my money on the table, highest bid on the table) in this auction."""
    on_table = (state[:se.N_MONEY, :se.MAX_PLAYERS] == -1) * _MONEY[:, None]
    totals = on_table.sum(0)
    return int(totals[0]), int(totals.max())


def legal_bids(state):
    """
    Indices into SUBSETS of every legal move: pass (0), plus every set of cards
    from my hand that, added to what I already have on the table, beats the
    high bid while keeping at most MAX_BID_CARDS cards on the table.
    """
    hand = state[:se.N_MONEY, 0] == 1
    n_table = int((state[:se.N_MONEY, 0] == -1).sum())
    mine, top = bid_totals(state)
    ok = ((SUBSETS[:, ~hand].sum(1) == 0)
          & (SUBSET_SIZE + n_table <= MAX_BID_CARDS)
          & (mine + SUBSET_VALUE > top))
    ok[PASS_CAND] = True
    return np.flatnonzero(ok)


def afterstates(state, cands):
    """(K, 27, 6): the state as it would look right after each candidate move."""
    after = np.repeat(state[None], len(cands), axis=0)
    after[:, :se.N_MONEY, 0] -= 2 * SUBSETS[cands]          # hand (1) -> on the table (-1)
    g = se.GAME_COL
    is_pass = cands == PASS_CAND
    after[is_pass, se.IN_AUCTION0, g] = 0                    # pass: I'm out of this auction
    bid = ~is_pass
    after[bid, se.HIGH_BIDDER0:se.HIGH_BIDDER0 + se.MAX_PLAYERS, g] = 0
    after[bid, se.HIGH_BIDDER0, g] = 1                       # bid: I hold the high bid
    return after


def candidate_to_vector(cand):
    """SUBSETS index -> the 12-entry output vector the seat expects."""
    v = np.zeros(N_BID_OUT, dtype=np.int8)
    if cand == PASS_CAND:
        v[PASS] = 1
    else:
        v[:se.N_MONEY] = SUBSETS[cand]
    return v


def vector_to_candidate(vec):
    """12-entry output vector -> SUBSETS index (None if it adds more than MAX_BID_CARDS cards)."""
    vec = np.asarray(vec)
    if vec[PASS] or not vec[:se.N_MONEY].any():
        return PASS_CAND
    return _SUBSET_INDEX.get((vec[:se.N_MONEY] != 0).astype(np.int8).tobytes())


# ---------------------------------------------------------------- policies

class RandomPolicy:
    """Placeholder until there's a network: random legal moves."""

    def __init__(self, pass_prob=0.3, seed=None):
        self.pass_prob, self.rng = pass_prob, random.Random(seed)

    def bid(self, state, mask):
        out = np.zeros(N_BID_OUT, dtype=np.int8)
        hand = [i for i in range(se.N_MONEY) if mask[i]]
        mine = sum(se.MONEY[i] for i in range(se.N_MONEY) if state[i, 0] == -1)
        top = max(sum(se.MONEY[i] for i in range(se.N_MONEY) if state[i, c] == -1) for c in range(se.MAX_PLAYERS))
        self.rng.shuffle(hand)
        total, picked = mine, []
        for i in hand:
            if total > top:
                break
            picked.append(i)
            total += se.MONEY[i]
        if total <= top or self.rng.random() < self.pass_prob:
            out[PASS] = 1
        else:
            out[picked] = 1
        return out


# ---------------------------------------------------------------- the seat

class RLSeat(BasePlayer):
    def __init__(self, policy, name="RL Agent", username="rl agent"):
        super().__init__(name, username)
        self.active = True
        self.policy = policy
        self.players = None            # set by attach()
        self.decisions = []            # (state, mask, output, engine_move)
        self.invalid_moves = 0

    def attach(self, game):
        self.players = game.players

    def _state(self):
        return encode_live(self.players, self._auction_history_source,
                           self.get_live_auction_state().get("card"), self.players.index(self))

    def get_bid(self, timeout=None):
        state = self._state()
        mask = bid_mask(state)
        out = np.asarray(self.policy.bid(state, mask)) * mask
        move = self.decode_bid(state, out)
        self.decisions.append((state, mask, out, move))
        return move

    def decode_bid(self, state, out):
        if out[PASS] or not out[:se.N_MONEY].any():
            return "pass"
        cards = [se.MONEY[i] for i in np.flatnonzero(out[:se.N_MONEY])]
        mine = self.current_bid_value + sum(cards)
        top = max(p.current_bid_value for p in self.players)
        if mine <= top:                # doesn't beat the high bid: the engine would
            self.invalid_moves += 1    # just re-prompt forever, so pass instead
            return "pass"
        return cards

    def choose_painting_to_discard(self):
        # Always the lowest painting -- see the module docstring.
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return min(paintings, key=lambda c: c.value) if paintings else None

    def send_message(self, *args, **kwargs):
        pass


# ---------------------------------------------------------------- running a game

@contextlib.contextmanager
def _fast():
    """Bots sleep a human-watchable think time on every move; skip it."""
    from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
    original = time.sleep
    time.sleep = lambda *a, **k: None
    LoggingManager.set_level("WARNING")
    try:
        yield
    finally:
        time.sleep = original


def play_game(policy=None, opponents=("medium", "medium", "medium", "medium"), seat=0, seed=None):
    """
    Plays one game: an RLSeat at `seat` plus engine bots (`opponents`, 2-4 of
    "easy"/"medium"/"hard"/"greedy"/"capped"/"pass"). Returns
    (record, rl_seat): the replayable game record and the seat, whose
    .decisions holds every (state, mask, output, move) it made.
    """
    from highsociety.code.ai import create_bot_players
    from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
    from highsociety.code.gamecore.game_manager.gameplay import PlayGame
    from highsociety.code.gamecore.recording.game_replay import game_record

    rl = RLSeat(policy or RandomPolicy(seed=seed))
    with _fast():
        players = create_bot_players(list(opponents), think_time=0)
        players.insert(seat, rl)
        game = PlayGame(players=players, spectators=[], mode="network", seed=seed,
                        turn_duration=None, auction_history=AuctionHistory(), pace_events=False,
                        preseated=True)       # keep the RL seat where we put it
        rl.attach(game)
        game.play_game()
    return game_record(game), rl


def play_selfplay(policies, seed=None):
    """
    Plays one game with one seat per entry of `policies` (3-5 of them, in
    turn order). An entry is either a policy (that seat is an RLSeat) or an
    engine-bot name such as "greedy" / "capped" (that seat is an engine bot).
    Returns (record, seats): seats[i] played policies[i] and holds its own
    .decisions (None for an engine-bot seat), so record["standings"] seat i
    belongs to seats[i].
    """
    from highsociety.code.ai import create_bot_players
    from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
    from highsociety.code.gamecore.game_manager.gameplay import PlayGame
    from highsociety.code.gamecore.recording.game_replay import game_record

    if not 3 <= len(policies) <= 5:
        raise ValueError("self-play needs 3-5 policies")
    seats = [None if isinstance(p, str) else RLSeat(p, name=f"RL {i}", username=f"rl {i}")
             for i, p in enumerate(policies)]
    with _fast():
        bots = iter(create_bot_players([p for p in policies if isinstance(p, str)], think_time=0))
        players = [s if s is not None else next(bots) for s in seats]
        game = PlayGame(players=players, spectators=[], mode="network", seed=seed,
                        turn_duration=None, auction_history=AuctionHistory(), pace_events=False,
                        preseated=True)
        for s in seats:
            if s is not None:
                s.attach(game)
        game.play_game()
    return game_record(game), seats


# ---------------------------------------------------------------- data & evaluation

def build_dataset(records, perspective=True):
    """
    Self-play game records -> training arrays, one row per bid/pass decision
    (Faux Pas discards are automatic, so they're skipped). Returns a dict of
    numpy arrays:
        X       (N, 27, 6) int8  states
        mask    (N, 12)          legal outputs
        y       (N, 12)          the output vector the player actually chose
        won     (N,)             1 if the acting player won that game
        points  (N,)             acting player's final points
        game    (N,)             index of the game in `records` (for train/test splits by game)
    """
    d = {"X": [], "mask": [], "y": [], "won": [], "points": [], "game": []}
    for gi, rec in enumerate(records):
        final = {s["seat"]: s for s in rec["standings"]}
        for state, action in se.encode_game(rec, perspective=perspective):
            if action["action"] == "discard":
                continue
            d["X"].append(state)
            d["mask"].append(bid_mask(state))
            d["y"].append(action_to_vector(state, action))
            d["won"].append(int(final[action["seat"]]["is_winner"]))
            d["points"].append(final[action["seat"]]["points"])
            d["game"].append(gi)
    dtypes = {"X": np.int8, "mask": np.int8, "y": np.int8, "won": np.int8, "points": np.float32, "game": np.int32}
    return {f: np.array(v, dtype=dtypes[f]) for f, v in d.items()}


# ---------------------------------------------------------------- per-auction replay (for step rewards)

DECK_SIZE = 16                                  # 10 paintings + 3 prestige + 3 disgrace
GREEN = {"PrestigeCard", "Scandale"}            # the 4th green card revealed ends the game
_DISGRACE = {"FauxPas", "Passe", "Scandale"}


def _score(s):
    """Engine scoring: (paintings + Passe -5) x 2 per Prestige x 1/2 for Scandale."""
    pts = sum(s["paintings"]) - 5 * s["passe"]
    return pts * 2 ** s["prestige"] * (0.5 if s["scandale"] else 1)


def auction_ledger(record):
    """
    Replays a finished game auction by auction. Returns a dict:
        rounds          list, one entry per auctioned card, in order:
            card            card dict ({"type", "value"})
            recipient       seat that got it (None: nobody)
            disgrace        True for Faux Pas / Passe / Scandale
            paid            {seat: money paid this auction} -- normal auction: the winner pays
                            their bid; disgrace (engine default, forfeit): everyone EXCEPT the
                            player who took the card loses what they raised
            bidders         seats that put money down at least once this auction
            bids            {seat: their final (highest) bid this auction}
            spent_before / spent_after   {seat: total money spent so far}
            points_before / points_after {seat: score so far} (Faux Pas discards included)
            greens          green cards revealed so far, including this one
            left            cards still face-down after this one was drawn
        decision_rounds {seat: [round index of each of that seat's bid/pass decisions]}
                        -- lines up 1:1 with RLSeat.decisions
    Checks itself against the record's final standings (money left and points).
    """
    seats = [s["seat"] for s in record["seats"]]
    by_round = {}
    for a in record["actions"]:
        by_round.setdefault(a["round"], []).append(a)
    state = {s: {"paintings": [], "prestige": 0, "passe": 0, "scandale": 0} for s in seats}
    spent = {s: 0 for s in seats}
    rounds, decision_rounds, greens = [], {s: [] for s in seats}, 0
    for i, r in enumerate(record["rounds"]):
        acts = by_round.get(r["round"], [])
        points_before = {s: _score(state[s]) for s in seats}
        for a in acts:
            if a["action"] in ("bid", "pass"):
                decision_rounds[a["seat"]].append(i)
        last_bid = {}
        for a in acts:
            if a["action"] == "bid":
                last_bid[a["seat"]] = a["amount"]
        card, rec = r["card"], r["recipient_seat"]
        disgrace = card["type"] in _DISGRACE
        if disgrace:
            paid = {s: amt for s, amt in last_bid.items() if s != rec}
        else:
            paid = {rec: last_bid.get(rec, 0)} if rec is not None else {}
        spent_before = dict(spent)
        for s, amt in paid.items():
            spent[s] += amt
        if rec is not None:
            st, t = state[rec], card["type"]
            if t == "Painting":
                st["paintings"].append(card["value"])
            elif t == "PrestigeCard":
                st["prestige"] += 1
            elif t == "Passe":
                st["passe"] += 1
            elif t == "Scandale":
                st["scandale"] += 1
        for a in acts:                                       # Faux Pas discard (lowest painting):
            if a["action"] == "discard":                     # logged in the round it follows
                state[a["seat"]]["paintings"].remove(a["amount"])
        greens += card["type"] in GREEN
        rounds.append({"card": card, "recipient": rec, "disgrace": disgrace, "paid": paid,
                       "bidders": set(last_bid), "bids": last_bid,
                       "spent_before": spent_before, "spent_after": dict(spent),
                       "points_before": points_before, "points_after": {s: _score(state[s]) for s in seats},
                       "greens": greens, "left": DECK_SIZE - (i + 1)})
    for f in record["standings"]:
        s = f["seat"]
        assert 106 - spent[s] == f["money_left"], ("money", s, spent[s], f)
        assert abs(_score(state[s]) - f["points"]) < 1e-9, ("points", s, state[s], f)
    return {"rounds": rounds, "decision_rounds": decision_rounds}


def evaluate(policy, n_games=20, opponents=("greedy", "capped", "capped", "greedy"), seed0=0):
    """
    Plays n_games with `policy` in a seat that rotates each game, and returns
    a summary dict: win rate, average points/money, invalid moves, and the
    win rate a seat would get by chance (1 / number of players).
    """
    n_players = len(opponents) + 1
    wins = pts = money = invalid = 0
    for i in range(n_games):
        seat = i % n_players
        record, rl = play_game(policy, opponents=opponents, seat=seat, seed=seed0 + i)
        me = next(s for s in record["standings"] if s["seat"] == seat)
        wins += me["is_winner"]
        pts += me["points"]
        money += me["money_left"]
        invalid += rl.invalid_moves
    return {"games": n_games, "win_rate": wins / n_games, "chance": 1 / n_players,
            "avg_points": pts / n_games, "avg_money_left": money / n_games, "invalid_moves": invalid}
