import random

from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.common.utils.utility import get_all_configurations
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.recording.session_recorder import SessionRecorder
from highsociety.code.gamecore.recording.recording_player import RecordingPlayer
from highsociety.code.gamecore.recording.replay_player import ReplayPlayer

def get_num_players() -> int:
    """Prompt user for the number of players."""
    while True:
        try:
            num = int(input("Enter number of players: ").strip())
            if num < 2:
                print("⚠️ At least 2 players are required to start the game.")
                continue
            return num
        except ValueError:
            print("⚠️ Please enter a valid integer.")

def get_player_details(player_idx: int):
    """Collect name and username for each player."""
    print(f"--- Player {player_idx + 1} Details ---")
    username = input("Enter username: ").strip()
    name = input("Enter display name: ").strip()

    if not username:
        username = f"player{player_idx + 1}"
    if not name:
        name = username

    return username, name

def create_players(num_players: int) -> list[CLIPlayer]:
    """Create and return a list of CLIPlayer objects."""
    players = []
    for i in range(num_players):
        username, name = get_player_details(i)
        player = CLIPlayer(name=name, username=username)
        players.append(player)

    return players


def build_replay_players(recording: dict) -> list[ReplayPlayer]:
    """Reconstructs players from a recording, feeding back its exact decisions."""
    players = []
    for entry in recording["players"]:
        username = entry["username"]
        wrapped = CLIPlayer(name=entry["name"], username=username)
        players.append(ReplayPlayer(wrapped, recording["actions"].get(username, [])))
    return players


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='HighSociety CLI')
    parser.add_argument('--seed', type=int, default=None,
                       help='Seed the RNG for a fully reproducible game (deck order, player order, starting player)')
    parser.add_argument('--record', type=str, default=None, metavar='PATH',
                       help='Record every decision made this game to PATH, so it can be replayed later with --replay')
    parser.add_argument('--replay', type=str, default=None, metavar='PATH',
                       help='Replay a previously recorded session from PATH (skips interactive player setup)')
    args = parser.parse_args()

    config = get_all_configurations()
    logging_manager = LoggingManager(config)

    if args.replay:
        recording = SessionRecorder.load(args.replay)
        players = build_replay_players(recording)
        print(f"▶️  Replaying {args.replay} (seed={recording['seed']}, {len(players)} players)")
        game = PlayGame(players=players, mode='cli', seed=recording['seed'])
        game.play_game()
    else:
        num_players = get_num_players()
        players = create_players(num_players)
        seed = args.seed if args.seed is not None else random.randint(0, 2**31 - 1)

        if args.record:
            recorder = SessionRecorder(path=args.record, seed=seed)
            players = [RecordingPlayer(p, recorder) for p in players]
            print(f"⏺️  Recording this session to {args.record} (seed={seed})")

        game = PlayGame(players=players, mode='cli', seed=seed)
        game.play_game()