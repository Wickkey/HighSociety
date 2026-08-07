import time

from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.player.networkspectator import NetworkSpectator
from highsociety.code.common.utils.terminal_colors import style_game_event

class CLIHost:
    def __init__(self, players:list[CLIPlayer]):
        self.players = players

    def send_message(self, message: str, message_type: str = "GLOBAL_EVENT", data: dict = None):
        if message_type in ("AUCTION_RESULT", "AUCTION_UPDATE"):
            # Structured, bot/UI-facing data only (network mode broadcasts it
            # to remote clients/browsers) — CLI already narrated the
            # human-readable version of the same event via a separate
            # send_message call, so skip the redundant line here.
            return
        print(style_game_event(message))


class NetworkHost:
    def __init__(self, players: list[NetworkPlayer], spectators: list[NetworkSpectator]):
        self.players = players
        self.spectators = spectators

    def send_message(self, message: str, message_type: str = "GLOBAL_EVENT", data: dict = None):
        created_at = time.time()
        for player in self.players:
            if player.active:
                player.send_message(message, message_type=message_type, created_at=created_at, data=data)

        if len(self.spectators):
            for spectator in self.spectators:
                if spectator.active:
                    spectator.send_message(message, message_type=message_type, created_at=created_at, data=data)
        