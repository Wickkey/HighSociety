from typing import Union
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.components_module.status_card import StatusCard
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas, Passe, Scandale

class AuctionInformation:
    def __init__(self):
        self.round_number: int = None 
        self.auction_card: StatusCard = None
        self.bids = []

    def add_bid(self, 
                player_name: str, 
                player_username: str, 
                player_id: int, # range(num_players)
                player_current_participation_in_auction: bool,
                player_current_bid: int,
                player_current_money_card_bids: list[int]) -> None:
        
        bid_info = {
            "player_name": player_name,
            "player_username": player_username,
            "player_id": player_id,
            "player_current_participation_in_auction": player_current_participation_in_auction,
            "player_current_bid": player_current_bid,
            "player_current_money_card_bids": player_current_money_card_bids
        }
        self.bids.append(bid_info)
        LoggingManager.info(f"Adding bid info: {bid_info}")

