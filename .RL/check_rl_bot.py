"""
Checks the game's RL bot (highsociety/code/ai/rl/, numpy) against the training
code (rl_seat.py / rl_model.py, PyTorch) -- run it after every export:

    python check_rl_bot.py --weights magnus-v1 [--games 30]

Plays real games (3-5 players, RL bots + engine bots) and, at every decision of
every RL bot, compares:
    board       the bot's encoding (from the public AuctionHistory snapshot)
                == training's encode_live (from the live engine objects)
    moves       legal moves and their afterstates == training's
    scores      numpy network == PyTorch network on the same afterstates
    choice      the bot's move == training's greedy MyPolicy move
Exits 1 on any mismatch (score differences above --tol count as mismatches).
"""
import argparse
import json
import os
import sys
import time

os.environ["DATABASE_URL"] = ""          # never touch the real database

import numpy as np
import torch

import rl_seat as rs
from rl_model import MyPolicy, ScoreNet as TorchScoreNet

from highsociety.code.ai import create_bot_players
from highsociety.code.ai.rl import candidates, decision, encoding
from highsociety.code.ai.rl.network import WEIGHTS_DIR, load_network
from highsociety.code.ai.rl_bot import RLBot
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame


class CheckedBot(RLBot):
    """An RLBot that, at each decision, also runs the training code and records any difference."""

    def __init__(self, name, weights, torch_net, tol, report):
        super().__init__(name, name.lower(), weights=weights)
        self.policy = MyPolicy(torch_net, greedy=True)
        self.tol, self.report, self.players = tol, report, None

    def get_bid(self, timeout=None):
        t0 = time.perf_counter()
        board = encoding.encode(self.get_current_auction_history(), self.get_auction_history(),
                                self.get_live_auction_state(), self.username)
        move = decision.choose_move(self._net, board)
        self.report["ms"].append(1000 * (time.perf_counter() - t0))
        self.report["decisions"] += 1

        ref = rs.encode_live(self.players, self._auction_history_source,
                             self.get_live_auction_state().get("card"), self.players.index(self))
        if not np.array_equal(board, ref):
            self.report["board"] += 1
            if self.report["board"] <= 3:
                diff = np.argwhere(board != ref)
                print(f"  board mismatch ({self.username}): rows/cols {diff[:6].tolist()} "
                      f"bot {[int(board[r, c]) for r, c in diff[:6]]} vs training {[int(ref[r, c]) for r, c in diff[:6]]}")
            return candidates.move_cards(move) or "pass"

        moves = candidates.legal_moves(board)
        if not np.array_equal(moves, rs.legal_bids(ref)) or \
                not np.array_equal(candidates.afterstates(board, moves), rs.afterstates(ref, moves)):
            self.report["moves"] += 1
        after = candidates.afterstates(board, moves)
        with torch.no_grad():
            want = self.policy.model(torch.from_numpy(after).float()).numpy()
        err = float(np.abs(self._net.score(after) - want).max())
        self.report["max_err"] = max(self.report["max_err"], err)
        if err > self.tol:
            self.report["scores"] += 1
        if move != self.policy.choose(ref):
            self.report["choice"] += 1
        return "pass" if move == candidates.PASS else candidates.move_cards(move)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weights", default=None, help="weights name (default: the active Magnus weights)")
    p.add_argument("--games", type=int, default=30)
    p.add_argument("--tol", type=float, default=1e-3, help="max allowed |numpy - torch| score difference")
    a = p.parse_args()

    weights = a.weights or json.loads((WEIGHTS_DIR / "active.json").read_text())["magnus"]
    with np.load(WEIGHTS_DIR / f"{weights}.npz") as f:
        state_dict = {k: torch.from_numpy(f[k]) for k in f.files}
    torch_net = TorchScoreNet()
    torch_net.load_state_dict(state_dict)
    torch_net.eval()
    load_network(weights)

    report = {"decisions": 0, "board": 0, "moves": 0, "scores": 0, "choice": 0, "max_err": 0.0, "ms": []}
    rng = np.random.default_rng(0)
    with rs._fast():
        for g in range(a.games):
            n = [3, 4, 5][g % 3]
            n_rl = 1 + g % 2                                     # 1 or 2 RL bots per game
            bots = create_bot_players([str(b) for b in rng.choice(["capped", "greedy"], n - n_rl)], think_time=0)
            rl = [CheckedBot(f"Check{i}", weights, torch_net, a.tol, report) for i in range(n_rl)]
            players = rl + bots
            order = rng.permutation(n)
            players = [players[i] for i in order]
            game = PlayGame(players=players, spectators=[], mode="network", seed=70_000 + g, turn_duration=None,
                            auction_history=AuctionHistory(), pace_events=False, preseated=True)
            for b in rl:
                b.players = game.players
            game.play_game()

    ms = np.array(report["ms"])
    print(f"weights {weights}: {a.games} games, {report['decisions']} RL decisions")
    print(f"  board mismatches   {report['board']}")
    print(f"  move-list mismatch {report['moves']}")
    print(f"  score mismatches   {report['scores']}   (max |numpy - torch| = {report['max_err']:.2e}, tol {a.tol})")
    print(f"  choice mismatches  {report['choice']}")
    print(f"  decision time      median {np.median(ms):.1f} ms, p95 {np.percentile(ms, 95):.1f} ms, max {ms.max():.1f} ms")
    bad = report["board"] + report["moves"] + report["scores"] + report["choice"]
    print("OK" if bad == 0 else "MISMATCHES FOUND")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
