"""
The RL ("Magnus") bot: the network plumbing (encoding, legal moves, numpy
network) and the bot playing real games through PlayGame.

Its match against the PyTorch training code (same boards, same scores, same
moves) is checked outside the test suite by .RL/check_rl_bot.py, since that
needs PyTorch, which the app doesn't depend on.
"""
import numpy as np
import pytest

from highsociety.code.ai import BOT_TYPES, create_bot_players
from highsociety.code.ai.capped_greedy_bot import CappedGreedyBot
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.ai.rl import candidates, decision, encoding
from highsociety.code.ai.rl.network import load_network
from highsociety.code.ai.rl_bot import MagnusBot, RLBot, active_weights
from highsociety.code.common.bot_roster import DIFFICULTIES, names_for_difficulty
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame


class RecordingMagnus(MagnusBot):
    """A MagnusBot that keeps every board it saw and every move it made."""

    def __init__(self, name):
        super().__init__(name=name, username=name.lower())
        self.seen = []        # (board, live_state, snapshots-in-auction, move)

    def get_bid(self, timeout=None):
        board = encoding.encode(self.get_current_auction_history(), self.get_auction_history(),
                                self.get_live_auction_state(), self.username)
        in_auction = {u: s.in_auction for u, s in self.get_current_auction_history().player_snapshots.items()}
        move = super().get_bid(timeout)
        self.seen.append((board, self.get_live_auction_state(), in_auction, move))
        return move


def _play(players, seed):
    game = PlayGame(players=players, spectators=[], mode="network", seed=seed, turn_duration=None,
                    auction_history=AuctionHistory(), pace_events=False, preseated=True)
    game.play_game()
    return game


# ---------------------------------------------------------------- the bot in real games

@pytest.mark.parametrize("n_players", [3, 4, 5])
def test_magnus_plays_full_games_with_only_legal_moves(n_players):
    for seed in range(3):
        magnus = RecordingMagnus("Magnus")
        others = [CappedGreedyBot(f"Capped{i}", f"capped{i}") if i % 2 else GreedyBot(f"Greedy{i}", f"greedy{i}")
                  for i in range(n_players - 1)]
        game = _play([magnus, *others], seed=seed)
        assert game.auction_rounds, "the game never got going"
        assert magnus.seen, "Magnus never had to decide anything"
        for board, live, _, move in magnus.seen:
            if move == "pass":
                continue
            assert isinstance(move, list) and 1 <= len(move) <= candidates.MAX_BID_CARDS
            hand = [encoding.MONEY[i] for i in range(encoding.N_MONEY) if board[i, 0] == 1]
            assert all(v in hand for v in move), f"bid {move} with cards not in hand {hand}"
            mine, top = candidates.bid_totals(board)
            assert mine + sum(move) > top, "a bid that doesn't beat the high bid"


def test_two_magnus_bots_at_one_table_finish_the_game():
    game = _play([MagnusBot("Atlas Bot", "atlas bot"), MagnusBot("Vega Bot", "vega bot"),
                  CappedGreedyBot("Capped", "capped")], seed=7)
    assert game.auction_rounds


def test_the_board_matches_the_bot_and_the_table():
    magnus = RecordingMagnus("Magnus")
    _play([magnus, GreedyBot("G1", "g1"), CappedGreedyBot("C1", "c1")], seed=3)
    for board, live, _, _ in magnus.seen:
        assert board.shape == (encoding.N_ROWS, encoding.N_COLS) and board.dtype == np.int8
        assert not board[:, 3:5].any(), "3-player game: seat columns 3 and 4 must stay empty"
        assert (board[encoding.N_MONEY:, encoding.GAME_COL] == 2).sum() == 1, "exactly one card on auction"
        row = encoding.card_row(live["card"], 0)
        if live["card"]["type"] != "PrestigeCard":
            assert board[row, encoding.GAME_COL] == 2


class SnapshotChecker(MagnusBot):
    """At each decision, compares the AuctionHistory snapshot the bot reads with the live players."""

    def __init__(self, name):
        super().__init__(name=name, username=name.lower())
        self.players, self.decisions, self.stale = None, 0, []

    def get_bid(self, timeout=None):
        snaps = self.get_current_auction_history().player_snapshots
        for p in self.players:
            s = snaps[p.username]
            live = (p.current_participation_in_auction, sorted(c.value for c in p.current_money_card_bids),
                    sorted(c.value for c in p.money_cards))
            seen = (s.in_auction, sorted(s.current_money_card_bids), sorted(s.money_cards))
            if live != seen:
                self.stale.append((len(self.get_auction_history()), p.username, live, seen))
        self.decisions += 1
        return super().get_bid(timeout)


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_the_snapshot_the_bot_reads_is_current_at_every_decision(seed):
    # PlayGame snapshots at the end of an auction *before* resetting players for
    # the next one; without the fresh snapshot at auction start, the first turns
    # of each auction saw last auction's passes and bids.
    magnus = SnapshotChecker("Magnus")
    game = PlayGame(players=[magnus, GreedyBot("G1", "g1"), CappedGreedyBot("C1", "c1"), GreedyBot("G2", "g2")],
                    spectators=[], mode="network", seed=seed, turn_duration=None,
                    auction_history=AuctionHistory(), pace_events=False, preseated=True)
    magnus.players = game.players
    game.play_game()
    assert magnus.decisions > 0
    assert magnus.stale == []


# ---------------------------------------------------------------- the pieces

def test_legal_moves_start_with_pass_and_all_beat_the_high_bid():
    board = np.zeros((encoding.N_ROWS, encoding.N_COLS), dtype=np.int8)
    board[:encoding.N_MONEY, 0] = 1                               # full hand
    board[encoding.MONEY_ROW[20], 1] = -1                          # another player bid 20
    moves = candidates.legal_moves(board)
    assert moves[0] == candidates.PASS
    for m in moves[1:]:
        cards = candidates.move_cards(m)
        assert sum(cards) > 20 and len(cards) <= candidates.MAX_BID_CARDS


def test_afterstates_move_cards_to_the_table_and_take_the_high_bid():
    board = np.zeros((encoding.N_ROWS, encoding.N_COLS), dtype=np.int8)
    board[:encoding.N_MONEY, 0] = 1
    board[encoding.IN_AUCTION0, encoding.GAME_COL] = 1
    moves = candidates.legal_moves(board)
    after = candidates.afterstates(board, moves)
    assert after[0, encoding.IN_AUCTION0, encoding.GAME_COL] == 0          # pass: out of the auction
    bid = int(np.flatnonzero(moves == next(m for m in moves if candidates.move_cards(m) == [25]))[0])
    assert after[bid, encoding.MONEY_ROW[25], 0] == -1
    assert after[bid, encoding.HIGH_BIDDER0, encoding.GAME_COL] == 1


def test_the_active_weights_load_and_score_deterministically():
    net = load_network(active_weights("magnus"))
    boards = np.random.default_rng(0).integers(-1, 3, size=(7, encoding.N_ROWS, encoding.N_COLS)).astype(np.int8)
    scores = net.score(boards)
    assert scores.shape == (7,) and np.isfinite(scores).all()
    assert np.array_equal(scores, net.score(boards))
    assert load_network(active_weights("magnus")) is net                   # one shared copy


def test_faux_pas_discard_gives_up_the_cheapest_painting():
    cards = [{"type": "Painting", "value": 7}, {"type": "PrestigeCard", "value": 0},
             {"type": "Painting", "value": 2}, {"type": "Painting", "value": 9}]
    assert decision.decide_faux_pas_discard(cards) == 2
    assert decision.decide_faux_pas_discard([{"type": "Passe", "value": -5}]) is None

    bot = MagnusBot("M", "m")
    for v in (6, 3, 8):
        bot.add_status_card(Painting(value=v))
    assert bot.choose_painting_to_discard().value == 3


def test_a_failing_decision_passes_instead_of_breaking_the_game(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(decision, "decide_bid", boom)
    assert MagnusBot("M", "m").get_bid() == "pass"


# ---------------------------------------------------------------- the "magnus" difficulty

def test_magnus_is_a_lobby_difficulty_with_its_own_names():
    assert "magnus" in DIFFICULTIES
    assert BOT_TYPES["magnus"] is MagnusBot
    assert len(names_for_difficulty("magnus")) >= 4                  # a table seats at most 4 bots
    bots = create_bot_players(["magnus", "magnus", "hard"], think_time=0)
    assert [type(b) for b in bots] == [MagnusBot, MagnusBot, BOT_TYPES["hard"]]
    assert all(b.name in names_for_difficulty("magnus") for b in bots[:2])
    assert bots[0].difficulty == "magnus" and isinstance(bots[0], RLBot)
