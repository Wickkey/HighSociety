from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer

class CLIHost:
    def __init__(self, players:list[CLIPlayer]):
        self.players = players 

    def send_message(self, message:str):
        print(message)


class NetworkHost:
    def __init(self, players: list[NetworkPlayer]):
        self.players = players 

    def send_message(self, message: str):
        for player in self.players:
            player.send_message(message)
        