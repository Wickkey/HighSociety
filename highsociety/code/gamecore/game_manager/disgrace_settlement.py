from abc import ABC, abstractmethod
from highsociety.code.gamecore.player.player import BasePlayer


class DisgraceAuctionSettlement(ABC):
    """
    Decides what happens to bid money once a disgrace-card auction concludes.

    Called after the loser has been determined (and has already taken the
    card) but before auction attributes are reset for the next round, so
    implementations can inspect/return each player's committed bid cards.
    """

    @abstractmethod
    def settle(self, players: list[BasePlayer], loser_id: int) -> None:
        ...


class ForfeitSettlement(DisgraceAuctionSettlement):
    """
    Default rule for this implementation: the player who takes the disgrace
    card keeps whatever they had already withdrawn when they passed. Every
    other player forfeits the money cards they committed while raising —
    those cards are simply never returned to their MoneyCardManager.
    """

    def settle(self, players: list[BasePlayer], loser_id: int) -> None:
        return  # forfeiture is the absence of a refund; nothing to do here.


class RefundAllSettlement(DisgraceAuctionSettlement):
    """
    Standard High Society rule: nobody pays to avoid a disgrace card. All
    money committed during the auction, by every player, is returned.
    """

    def settle(self, players: list[BasePlayer], loser_id: int) -> None:
        for player in players:
            player.withdraw_bid()
