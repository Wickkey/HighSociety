from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.utils.utility import get_all_configurations
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.game_manager.host import CLIPlayer, CLIHost
from highsociety.code.gamecore.player.cliplayer import CLIPlayer

import sys
sys.path.insert(0, '/Users/vignesh/Documents/HighSociety')

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
    print(f"\n--- Player {player_idx + 1} Details ---")
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


if __name__ == '__main__':
    config = get_all_configurations()
    logging_manager = LoggingManager(config)

    num_players = get_num_players()
    players = create_players(num_players)

    game = PlayGame(players=players, mode='cli')
    game.play_game()