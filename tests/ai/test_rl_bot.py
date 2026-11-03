"""
The RL ("Insane") bot: the network plumbing (encoding, legal moves, numpy
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
from highsociety.code.ai.rl_bot import InsaneBot, RLBot, active_weights
from highsociety.code.common.bot_roster import DIFFICULTIES, names_for_difficulty
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame


class RecordingInsane(InsaneBot):
    """A InsaneBot that keeps every board it saw and every move it made."""

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
def test_insane_plays_full_games_with_only_legal_moves(n_players):
    for seed in range(3):
        bot = RecordingInsane("Insane")
        others = [CappedGreedyBot(f"Capped{i}", f"capped{i}") if i % 2 else GreedyBot(f"Greedy{i}", f"greedy{i}")
                  for i in range(n_players - 1)]
        game = _play([bot, *others], seed=seed)
        assert game.auction_rounds, "the game never got going"
        assert bot.seen, "the bot never had to decide anything"
        for board, live, _, move in bot.seen:
            if move == "pass":
                continue
            assert isinstance(move, list) and 1 <= len(move) <= candidates.MAX_BID_CARDS
            hand = [encoding.MONEY[i] for i in range(encoding.N_MONEY) if board[i, 0] == 1]
            assert all(v in hand for v in move), f"bid {move} with cards not in hand {hand}"
            mine, top = candidates.bid_totals(board)
            assert mine + sum(move) > top, "a bid that doesn't beat the high bid"


def test_two_insane_bots_at_one_table_finish_the_game():
    game = _play([InsaneBot("Batman Bot", "batman bot"), InsaneBot("Thanos Bot", "thanos bot"),
                  CappedGreedyBot("Capped", "capped")], seed=7)
    assert game.auction_rounds


def test_the_board_matches_the_bot_and_the_table():
    bot = RecordingInsane("Insane")
    _play([bot, GreedyBot("G1", "g1"), CappedGreedyBot("C1", "c1")], seed=3)
    for board, live, _, _ in bot.seen:
        assert board.shape == (encoding.N_ROWS, encoding.N_COLS) and board.dtype == np.int8
        assert not board[:, 3:5].any(), "3-player game: seat columns 3 and 4 must stay empty"
        assert (board[encoding.N_MONEY:, encoding.GAME_COL] == 2).sum() == 1, "exactly one card on auction"
        row = encoding.card_row(live["card"], 0)
        if live["card"]["type"] != "PrestigeCard":
            assert board[row, encoding.GAME_COL] == 2


class SnapshotChecker(InsaneBot):
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
    bot = SnapshotChecker("Insane")
    game = PlayGame(players=[bot, GreedyBot("G1", "g1"), CappedGreedyBot("C1", "c1"), GreedyBot("G2", "g2")],
                    spectators=[], mode="network", seed=seed, turn_duration=None,
                    auction_history=AuctionHistory(), pace_events=False, preseated=True)
    bot.players = game.players
    game.play_game()
    assert bot.decisions > 0
    assert bot.stale == []


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
    net = load_network(active_weights("insane"))
    boards = np.random.default_rng(0).integers(-1, 3, size=(7, encoding.N_ROWS, encoding.N_COLS)).astype(np.int8)
    run = net.score if net.arch == "v1" else net.logits
    scores = run(boards)
    assert scores.shape == ((7,) if net.arch == "v1" else (7, len(candidates.SUBSETS))) and np.isfinite(scores).all()
    assert np.array_equal(scores, run(boards))
    assert load_network(active_weights("insane")) is net                   # one shared copy


def test_faux_pas_discard_gives_up_the_cheapest_painting():
    cards = [{"type": "Painting", "value": 7}, {"type": "PrestigeCard", "value": 0},
             {"type": "Painting", "value": 2}, {"type": "Painting", "value": 9}]
    assert decision.decide_faux_pas_discard(cards) == 2
    assert decision.decide_faux_pas_discard([{"type": "Passe", "value": -5}]) is None

    bot = InsaneBot("M", "m")
    for v in (6, 3, 8):
        bot.add_status_card(Painting(value=v))
    assert bot.choose_painting_to_discard().value == 3


def test_a_failing_decision_passes_instead_of_breaking_the_game(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(decision, "decide_bid", boom)
    assert InsaneBot("M", "m").get_bid() == "pass"


# ---------------------------------------------------------------- the "insane" difficulty

def test_insane_is_a_lobby_difficulty_with_its_own_names():
    assert "insane" in DIFFICULTIES
    assert BOT_TYPES["insane"] is InsaneBot
    assert len(names_for_difficulty("insane")) >= 4                  # a table seats at most 4 bots
    bots = create_bot_players(["insane", "insane", "hard"], think_time=0)
    assert [type(b) for b in bots] == [InsaneBot, InsaneBot, BOT_TYPES["hard"]]
    assert all(b.name in names_for_difficulty("insane") for b in bots[:2])
    assert bots[0].difficulty == "insane" and isinstance(bots[0], RLBot)


# ---------------------------------------------------------------- both network types

def _random_weights(head, d=32, layers=2, hidden=256, seed=0):
    rng = np.random.default_rng(seed)
    w = {"embed.weight": rng.normal(size=(d, 6)), "embed.bias": rng.normal(size=d), "card_id": rng.normal(size=(27, d))}
    for i in range(layers):
        p = f"attention.layers.{i}."
        w.update({p + "norm1.weight": np.ones(d), p + "norm1.bias": np.zeros(d),
                  p + "norm2.weight": np.ones(d), p + "norm2.bias": np.zeros(d),
                  p + "self_attn.in_proj_weight": rng.normal(size=(3 * d, d)) * 0.1, p + "self_attn.in_proj_bias": np.zeros(3 * d),
                  p + "self_attn.out_proj.weight": rng.normal(size=(d, d)) * 0.1, p + "self_attn.out_proj.bias": np.zeros(d),
                  p + "linear1.weight": rng.normal(size=(4 * d, d)) * 0.1, p + "linear1.bias": np.zeros(4 * d),
                  p + "linear2.weight": rng.normal(size=(d, 4 * d)) * 0.1, p + "linear2.bias": np.zeros(d)})
    if head == "v1":
        w.update({"head.1.weight": rng.normal(size=(hidden, 27 * d)) * 0.01, "head.1.bias": np.zeros(hidden),
                  "head.3.weight": rng.normal(size=(1, hidden)) * 0.01, "head.3.bias": np.zeros(1)})
    else:
        w.update({"body.1.weight": rng.normal(size=(hidden, 27 * d)) * 0.01, "body.1.bias": np.zeros(hidden),
                  "policy.weight": rng.normal(size=(len(candidates.SUBSETS), hidden)) * 0.01,
                  "policy.bias": np.zeros(len(candidates.SUBSETS))})
    return w


@pytest.mark.parametrize("arch", ["v1", "v2"])
def test_both_network_types_choose_a_legal_move(arch):
    from highsociety.code.ai.rl.network import PolicyNet, ScoreNet
    net = (ScoreNet if arch == "v1" else PolicyNet)(_random_weights(arch), n_heads=4, n_layers=2)
    assert net.arch == arch
    board = np.zeros((encoding.N_ROWS, encoding.N_COLS), dtype=np.int8)
    board[:encoding.N_MONEY, 0] = 1
    board[encoding.MONEY_ROW[8], 1] = -1
    move = decision.choose_move(net, board)
    assert move in candidates.legal_moves(board)


def test_the_shipped_weights_say_which_network_type_they_are():
    import json
    from highsociety.code.ai.rl.network import WEIGHTS_DIR
    name = active_weights("insane")
    manifest = json.loads((WEIGHTS_DIR / f"{name}.json").read_text())
    assert manifest["arch"] == load_network(name).arch
