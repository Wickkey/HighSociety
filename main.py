import random

from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.common.utils.utility import get_all_configurations, validate_player_count
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.recording.session_recorder import SessionRecorder
from highsociety.code.gamecore.recording.recording_player import RecordingPlayer
from highsociety.code.gamecore.recording.replay_player import ReplayPlayer
from highsociety.code.ai import BOT_TYPES, create_bot_players

def get_num_players() -> int:
    """Prompt user for the number of players, enforcing HSConfig.json's min_players/max_players."""
    while True:
        try:
            num = int(input("Enter number of players: ").strip())
            error = validate_player_count(num)
            if error:
                print(f"⚠️ {error}")
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

def create_players(num_players: int, bot_mix: list[str] = None, bot_think_time: float = 1.5) -> list:
    """
    Create and return the full seat list: bot_mix fills that many seats
    without prompting, then CLIPlayer prompts interactively for the rest.
    """
    players = create_bot_players(bot_mix, bot_think_time) if bot_mix else []
    for i in range(len(players), num_players):
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
    parser.add_argument('--bots', type=str, default=None,
                       help='Comma-separated bot types (see highsociety/code/ai/) to fill some seats '
                            'with, e.g. --bots greedy,pass — you are only prompted for the rest.')
    parser.add_argument('--bot-think-time', type=float, default=1.5,
                       help='Seconds each bot pauses before announcing a decision (default: 1.5). '
                            'Only matters if --bots is given.')
    args = parser.parse_args()

    config = get_all_configurations()
    logging_manager = LoggingManager(config)

    if args.replay:
        recording = SessionRecorder.load(args.replay)
        players = build_replay_players(recording)
        print(f"▶️  Replaying {args.replay} (seed={recording['seed']}, {len(players)} players)")
        game = PlayGame(players=players, mode='cli', seed=recording['seed'], auction_history=AuctionHistory())
        game.play_game()
    else:
        bot_mix = [b.strip() for b in args.bots.split(',') if b.strip()] if args.bots else []
        unknown = set(bot_mix) - set(BOT_TYPES)
        if unknown:
            parser.error(f"Unknown bot type(s) {sorted(unknown)}; choose from {list(BOT_TYPES)}")

        num_players = get_num_players()
        if len(bot_mix) > num_players:
            parser.error(f"--bots has {len(bot_mix)} entries but only {num_players} players were requested")

        players = create_players(num_players, bot_mix, args.bot_think_time)
        seed = args.seed if args.seed is not None else random.randint(0, 2**31 - 1)

        if args.record:
            recorder = SessionRecorder(path=args.record, seed=seed)
            players = [RecordingPlayer(p, recorder) for p in players]
            print(f"⏺️  Recording this session to {args.record} (seed={seed})")

        game = PlayGame(players=players, mode='cli', seed=seed, auction_history=AuctionHistory())
        game.play_game()