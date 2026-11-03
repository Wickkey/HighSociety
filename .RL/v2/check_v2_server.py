"""
Checks the game's RL bot running a v2 weights file (numpy, highsociety/code/ai/rl/) against the v2 training
code (PyTorch, v2/model_v2.py) at every decision of real games:

    python v2/check_v2_server.py --weights insane-v1 [--games 30]           (from .RL)

    board    the bot's encoding (public AuctionHistory snapshot) == training's encode_live (live engine objects)
    scores   numpy logits == PyTorch logits for every legal move (|diff| <= --tol)
    choice   the bot's move == training's greedy PolicyV2 move
Exits 1 on any mismatch.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["DATABASE_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                                        # noqa: E402
import torch                                                              # noqa: E402

import model_v2 as m2                                                     # noqa: E402
import rl_seat as rs                                                      # noqa: E402
from highsociety.code.ai import create_bot_players                        # noqa: E402
from highsociety.code.ai.rl import candidates, decision, encoding         # noqa: E402
from highsociety.code.ai.rl.network import WEIGHTS_DIR                     # noqa: E402
from highsociety.code.ai.rl_bot import RLBot                               # noqa: E402
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory   # noqa: E402
from highsociety.code.gamecore.game_manager.gameplay import PlayGame       # noqa: E402


class CheckedBot(RLBot):
    def __init__(self, name, weights, torch_net, tol, report):
        super().__init__(name, name.lower(), weights=weights)
        self.ref = m2.PolicyV2(torch_net, greedy=True)
        self.torch_net, self.tol, self.report, self.players = torch_net, tol, report, None

    def get_bid(self, timeout=None):
        t0 = time.perf_counter()
        board = encoding.encode(self.get_current_auction_history(), self.get_auction_history(),
                                self.get_live_auction_state(), self.username)
        move = decision.choose_move(self._net, board)
        self.report["ms"].append(1000 * (time.perf_counter() - t0))
        self.report["decisions"] += 1
        ref_board = rs.encode_live(self.players, self._auction_history_source,
                                   self.get_live_auction_state().get("card"), self.players.index(self))
        if not np.array_equal(board, ref_board):
            self.report["board"] += 1
        else:
            moves = candidates.legal_moves(board)
            with torch.no_grad():
                want = self.torch_net(torch.from_numpy(board[None]).float())[0][0].numpy()[moves]
            err = float(np.abs(self._net.logits(board[None])[0][moves] - want).max())
            self.report["max_err"] = max(self.report["max_err"], err)
            self.report["scores"] += err > self.tol
            self.report["choice"] += move != self.ref.choose(ref_board)
        return "pass" if move == candidates.PASS else candidates.move_cards(move)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--weights", default=None, help='weights name (default: the active "insane" weights)')
    p.add_argument("--games", type=int, default=30)
    p.add_argument("--tol", type=float, default=1e-3)
    a = p.parse_args()
    torch.set_num_threads(1)
    weights = a.weights or json.loads((WEIGHTS_DIR / "active.json").read_text())["insane"]
    with np.load(WEIGHTS_DIR / f"{weights}.npz") as f:
        sd = {k: torch.from_numpy(f[k]) for k in f.files}
    torch_net = m2.PolicyNet(value_head=False)
    torch_net.load_state_dict(sd)
    torch_net.eval()
    report = {"decisions": 0, "board": 0, "scores": 0, "choice": 0, "max_err": 0.0, "ms": []}
    rng = np.random.default_rng(0)
    with rs._fast():
        for g in range(a.games):
            n = (3, 4, 5)[g % 3]
            n_rl = 1 + g % 2
            bots = create_bot_players([str(b) for b in rng.choice(["capped", "greedy"], n - n_rl)], think_time=0)
            rl = [CheckedBot(f"Check{i}", weights, torch_net, a.tol, report) for i in range(n_rl)]
            players = [(rl + bots)[i] for i in rng.permutation(n)]
            game = PlayGame(players=players, spectators=[], mode="network", seed=70_000 + g, turn_duration=None,
                            auction_history=AuctionHistory(), pace_events=False, preseated=True)
            for b in rl:
                b.players = game.players
            game.play_game()
    ms = np.array(report["ms"])
    print(f"weights {weights}: {a.games} games, {report['decisions']} RL decisions")
    print(f"  board mismatches  {report['board']}")
    print(f"  score mismatches  {report['scores']}   (max |numpy - torch| = {report['max_err']:.2e}, tol {a.tol})")
    print(f"  choice mismatches {report['choice']}")
    print(f"  decision time     median {np.median(ms):.1f} ms, p95 {np.percentile(ms, 95):.1f} ms, max {ms.max():.1f} ms")
    bad = report["board"] + report["scores"] + report["choice"]
    print("OK" if bad == 0 else "MISMATCHES FOUND")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
