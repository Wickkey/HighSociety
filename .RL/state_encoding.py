"""
State encoding for RL: one decision point -> a (27, 6) matrix.

Rows (27) = 11 money rows + 16 status-card rows, shared by every column:

    row  0..10  money cards   1 2 3 4 6 8 10 12 15 20 25
    row 11..20  paintings     1 .. 10
    row 21..23  Prestige #1, #2, #3   (in the order they come out of the deck)
    row 24      Faux Pas
    row 25      Passe (-5)
    row 26      Scandale (x1/2)

Columns (6):

    col 0..4  players. col 0 is ALWAYS the player making this decision, then
              the others in turn order after them (perspective=True). Seats
              that don't exist (3- or 4-player game) are all zeros.
              money rows:   1 = in hand, -1 = on the table in the current bid,
                            0 = spent / forfeited
              status rows:  1 = this player owns that card, else 0

    col 5     the game.
              status rows:  0 = already auctioned, 1 = still in the deck,
                            2 = the card being auctioned right now
              money rows (the game has no money, so these 11 slots carry the
                          auction context, in the same rotated player order):
                rows 0..4   1 = player is still in this auction (hasn't passed)
                rows 5..9   1 = player holding the current highest bid
                row  10     1 = this decision is a Faux Pas discard, not a bid
                            (discards are automatic for the RL seat, so this is
                            always 0 in model inputs -- a spare slot)

Transpose (state.T) for the (players, features) layout most RL libraries use.
"""

import numpy as np

MONEY = [1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25]
N_MONEY = len(MONEY)                          # 11
PAINT0 = N_MONEY                              # paintings 1..10 -> rows 11..20
PRESTIGE0 = PAINT0 + 10                       # rows 21..23
FAUX_PAS, PASSE, SCANDALE = PRESTIGE0 + 3, PRESTIGE0 + 4, PRESTIGE0 + 5
N_ROWS = SCANDALE + 1                         # 27
MAX_PLAYERS = 5
GAME_COL = MAX_PLAYERS                        # col 5
IN_AUCTION0, HIGH_BIDDER0, DISCARD_FLAG = 0, MAX_PLAYERS, 2 * MAX_PLAYERS

MONEY_ROW = {v: i for i, v in enumerate(MONEY)}
ROW_NAMES = ([f"${v}" for v in MONEY] + [f"Painting {v}" for v in range(1, 11)]
             + ["Prestige #1", "Prestige #2", "Prestige #3", "Faux Pas", "Passe", "Scandale"])
GAME_MONEY_ROW_NAMES = ([f"in auction p{i}" for i in range(5)]
                        + [f"high bidder p{i}" for i in range(5)] + ["discard decision"])

DISGRACE = {"FauxPas", "Passe", "Scandale"}


class _Game:
    """Tracks the state of one game while walking its action log."""

    def __init__(self, record):
        self.n = len(record["seats"])
        self.hand = [set(MONEY) for _ in range(self.n)]   # money cards in hand
        self.table = [set() for _ in range(self.n)]       # committed this auction
        self.owned = [set() for _ in range(self.n)]       # status-card rows owned
        self.done = set()                                 # status-card rows auctioned
        self.current = None                               # row of the card on auction
        self.in_auction = [False] * self.n
        self.high_bidder = None
        self.prestige_seen = 0
        self.refund_all = record.get("settlement") == "RefundAllSettlement"

    def card_row(self, card):
        t, v = card["type"], card["value"]
        if t == "Painting":
            return PAINT0 + v - 1
        if t == "PrestigeCard":
            self.prestige_seen += 1
            return PRESTIGE0 + self.prestige_seen - 1
        return {"FauxPas": FAUX_PAS, "Passe": PASSE, "Scandale": SCANDALE}[t]

    def start_round(self, rnd):
        self.current = self.card_row(rnd["card"])
        self.disgrace = rnd["card"]["type"] in DISGRACE
        self.in_auction = [True] * self.n
        self.high_bidder = None

    def bid(self, seat, cards):
        cards = set(cards)
        if not self.table[seat] <= cards or not (cards - self.table[seat]) <= self.hand[seat]:
            raise ValueError(f"seat {seat} bid {sorted(cards)} it can't make")
        self.hand[seat] -= cards
        self.table[seat] = cards
        self.high_bidder = seat

    def pass_(self, seat):
        # Passing always returns your own table (normal: you're out; disgrace: you take it).
        self.hand[seat] |= self.table[seat]
        self.table[seat] = set()
        self.in_auction[seat] = False

    def resolve(self, rnd):
        if self.current is None:
            return
        winner = rnd["recipient_seat"]
        if not self.disgrace and winner is not None:
            paid = sum(self.table[winner])
            if paid != (rnd["price"] or 0):
                raise ValueError(f"round {rnd['round']}: winner paid {paid}, record says {rnd['price']}")
        for s in range(self.n):
            if self.disgrace and self.refund_all:
                self.hand[s] |= self.table[s]
            self.table[s] = set()        # normal: winner pays; disgrace: others forfeit
        if winner is not None:
            self.owned[winner].add(self.current)
        self.done.add(self.current)
        self.current = None
        self.in_auction = [False] * self.n
        self.high_bidder = None

    def discard(self, seat, value):
        self.owned[seat].discard(PAINT0 + value - 1)

    def encode(self, me, discard=False, perspective=True):
        order = [(me + k) % self.n for k in range(self.n)] if perspective else list(range(self.n))
        x = np.zeros((N_ROWS, MAX_PLAYERS + 1), dtype=np.int8)
        for col, s in enumerate(order):
            for v in self.hand[s]:
                x[MONEY_ROW[v], col] = 1
            for v in self.table[s]:
                x[MONEY_ROW[v], col] = -1
            for r in self.owned[s]:
                x[r, col] = 1
            x[IN_AUCTION0 + col, GAME_COL] = int(self.in_auction[s])
            x[HIGH_BIDDER0 + col, GAME_COL] = int(self.high_bidder == s)
        x[N_MONEY:, GAME_COL] = 1                # still in the deck
        for r in self.done:
            x[r, GAME_COL] = 0
        if self.current is not None:
            x[self.current, GAME_COL] = 2
        x[DISCARD_FLAG, GAME_COL] = int(discard)
        return x


def encode_game(record, perspective=True):
    """
    Walks one game record (a selfplay_games `replay`) and returns a list of
    (state, action) pairs, one per decision: `state` is the (27, 6) matrix the
    acting player saw just BEFORE making `action` (the raw action row).
    """
    g = _Game(record)
    rounds = {r["round"]: r for r in record["rounds"]}
    out, cur_round = [], None

    for a in sorted(record["actions"], key=lambda a: a["seq"]):
        kind, seat = a["action"], a["seat"]
        if kind in ("disconnect", "resign", "reconnect"):
            continue
        if a["round"] != cur_round:                      # new round starts
            if cur_round is not None:
                g.resolve(rounds[cur_round])
            cur_round = a["round"]
            g.start_round(rounds[cur_round])

        if kind == "discard":
            g.resolve(rounds[cur_round])                 # discards follow the auction
            out.append((g.encode(seat, discard=True, perspective=perspective), a))
            g.discard(seat, a["amount"])
            continue

        out.append((g.encode(seat, perspective=perspective), a))
        if kind == "bid":
            g.bid(seat, a["cards"])
        elif kind in ("pass", "fold", "quit"):
            g.pass_(seat)
        else:
            raise ValueError(f"unknown action {kind}")

    if cur_round is not None:
        g.resolve(rounds[cur_round])
    _check_final(g, record)
    return out


def _check_final(g, record):
    """Money left must match the recorded standings, or the walk is wrong."""
    for st in record["standings"]:
        have = sum(g.hand[st["seat"]])
        if have != st["money_left"]:
            raise ValueError(f"seat {st['seat']}: tracked ${have}, record says ${st['money_left']}")


def show(state):
    """Readable table of one state (rows = cards, cols = p0..p4, game)."""
    import pandas as pd
    df = pd.DataFrame(state, index=ROW_NAMES, columns=["p0 (me)", "p1", "p2", "p3", "p4", "game"])
    df.insert(0, "game money-row meaning", GAME_MONEY_ROW_NAMES + [""] * (N_ROWS - N_MONEY))
    return df
