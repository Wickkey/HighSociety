"""
The board as the RL network sees it: one decision point -> a (27, 6) int8 matrix.

This is the production copy of the encoding the network was trained on (.RL/
state_encoding.py + .RL/rl_seat.py's encode_live, which build it from a game
record / from live engine objects). It must stay identical to those, or the
network sees boards it never trained on -- .RL/v2/check_v2_server.py compares the
two at every decision of real games.

Rows (27) = 11 money rows + 16 status-card rows:

    row  0..10  money cards   1 2 3 4 6 8 10 12 15 20 25
    row 11..20  paintings     1 .. 10
    row 21..23  Prestige #1, #2, #3   (in the order they come out of the deck)
    row 24      Faux Pas
    row 25      Passe (-5)
    row 26      Scandale (x1/2)

Columns (6):

    col 0..4  players: col 0 is the player deciding, then the others in turn
              order after them. Seats that don't exist (3-/4-player game) are 0.
              money rows:   1 = in hand, -1 = on the table in the current bid,
                            0 = spent / forfeited
              status rows:  1 = this player owns that card
    col 5     the game.
              status rows:  0 = already auctioned, 1 = still in the deck,
                            2 = the card being auctioned right now
              money rows carry the auction context, in the same player order:
                rows 0..4   1 = player is still in this auction (hasn't passed)
                rows 5..9   1 = player holding the current highest bid
                row  10     always 0 here (a training-data flag for Faux Pas
                            discards, which the bot never asks the network about)

Built from public information only, the same three inputs MCTSBot uses (see
mcts/stateless_decision.py): the room's AuctionHistory snapshot (every
player's money cards, bids on the table, status cards, still-in-auction flag),
the completed-auction log, and the live auction state (the card up now).
Everyone's money is public in High Society -- spent cards are seen when bid --
so the other players' hands are known information, not a peek.
"""
import numpy as np

MONEY = (1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25)
N_MONEY = len(MONEY)                                   # 11
MONEY_ROW = {v: i for i, v in enumerate(MONEY)}
PAINT0 = N_MONEY                                       # paintings 1..10 -> rows 11..20
PRESTIGE0 = PAINT0 + 10                                # rows 21..23
FAUX_PAS, PASSE, SCANDALE = PRESTIGE0 + 3, PRESTIGE0 + 4, PRESTIGE0 + 5
N_ROWS = SCANDALE + 1                                  # 27
MAX_PLAYERS = 5
GAME_COL = MAX_PLAYERS                                 # 5
N_COLS = MAX_PLAYERS + 1                               # 6
IN_AUCTION0, HIGH_BIDDER0 = 0, MAX_PLAYERS             # game-column rows 0..4 and 5..9

_DISGRACE_ROW = {"FauxPas": FAUX_PAS, "Passe": PASSE, "Scandale": SCANDALE}


def card_row(card: dict, prestige_slot: int) -> int:
    """Row of a status card ({"type", "value", ...}). Prestige cards have no
    identity of their own, so they take slots #1..#3 in the order they appear."""
    kind = card["type"]
    if kind == "Painting":
        return PAINT0 + card["value"] - 1
    if kind == "PrestigeCard":
        return PRESTIGE0 + prestige_slot
    return _DISGRACE_ROW[kind]


def encode(auction_history, event_log: list[dict], live_state: dict, username: str) -> np.ndarray:
    """
    The (27, 6) board for `username`'s decision right now.

    auction_history: the room's AuctionHistory (player_snapshots in turn order).
    event_log:       completed auctions, oldest first (BotInterface.get_auction_history()).
    live_state:      BotInterface.get_live_auction_state() -- "card" is the card up now.
    """
    snaps = list(auction_history.player_snapshots.values())        # turn order
    n = len(snaps)
    me = next(i for i, s in enumerate(snaps) if s.username == username)
    order = [(me + k) % n for k in range(n)]                        # me first, then turn order
    col_of = {snaps[s].username: col for col, s in enumerate(order)}

    x = np.zeros((N_ROWS, N_COLS), dtype=np.int8)
    x[N_MONEY:, GAME_COL] = 1                                       # every status card: still in the deck

    prestige = 0
    for record in event_log:                                        # cards already auctioned
        row = card_row(record["card"], prestige)
        if record["card"]["type"] == "PrestigeCard":
            prestige += 1
            if record["recipient"] is not None:                     # prestige ownership needs the slot order
                x[row, col_of[record["recipient"]]] = 1
        x[row, GAME_COL] = 0

    for s in order:
        snap, col = snaps[s], col_of[snaps[s].username]
        for v in snap.money_cards:
            x[MONEY_ROW[v], col] = 1
        for v in snap.current_money_card_bids:
            x[MONEY_ROW[v], col] = -1
        for card in snap.status_cards:                              # prestige: handled above
            if card["type"] != "PrestigeCard":
                x[card_row(card, 0), col] = 1

    card = live_state.get("card")
    if card is not None:
        x[card_row(card, prestige), GAME_COL] = 2
        bids = {s.username: sum(s.current_money_card_bids) for s in snaps}
        top = max(bids.values())
        high_bidder = next((s for s in order if top and bids[snaps[s].username] == top), None)
        for s in order:
            col = col_of[snaps[s].username]
            x[IN_AUCTION0 + col, GAME_COL] = int(snaps[s].in_auction)
            x[HIGH_BIDDER0 + col, GAME_COL] = int(s == high_bidder)
    return x
