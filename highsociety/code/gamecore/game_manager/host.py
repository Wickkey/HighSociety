import time

from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.player.networkspectator import NetworkSpectator

class CLIHost:
    def __init__(self, players:list[CLIPlayer]):
        self.players = players 

    def send_message(self, message:str):
        print(message)


class NetworkHost:
    def __init__(self, players: list[NetworkPlayer], spectators: list[NetworkSpectator]):
        self.players = players 
        self.spectators = spectators

    def send_message(self, message: str):
        created_at = time.time()
        for player in self.players:
            player.send_message(message, message_type="GLOBAL_EVENT", created_at=created_at)

        if len(self.spectators):
            for spectator in self.spectators:
                if spectator.active:
                    spectator.send_message(message, message_type="GLOBAL_EVENT", created_at=created_at)
        