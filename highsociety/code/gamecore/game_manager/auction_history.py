import time
from dataclasses import dataclass, field

from highsociety.code.gamecore.game_manager.auction_information import summarize_card
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.player.player import BasePlayer


@dataclass
class PlayerSnapshot:
    username: str
    is_bot: bool
    money_cards: list = field(default_factory=list)
    # Every status card kind currently held (Painting, PrestigeCard, FauxPas,
    # Passe, Scandale), not just Paintings -- a stateless bot reconstructing
    # an opponent's true score/holdings, or the exact set of cards still left
    # in the deck, needs all of them, not just the subset that happens to be
    # Paintings. Each entry is summarize_card()'s plain-dict output.
    status_cards: list = field(default_factory=list)
    # Cards already committed to the CURRENT, still-unresolved auction --
    # distinct from money_cards, which correctly excludes these (see
    # BasePlayer.place_bid/withdraw_bid). Needed by "me" to know how much is
    # already on the table when computing a legal raise.
    current_money_card_bids: list = field(default_factory=list)
    points: int = 0
    holds_faux_pas: bool = False
    faux_pas_discarded: bool = False
    active: bool = True


class AuctionHistory:
    """
    Per-room "universal source of truth": an aggregated snapshot of every
    player's current state (money cards, status cards held, Faux Pas
    status), refreshed after every turn -- so any player/bot can read the
    game's current state at any time without replaying auction_rounds/
    AuctionRecord itself (that event-level log is unaffected by this and
    still exists separately, via PlayGame.get_auction_history() -- this is a
    complementary, aggregated *current-state* layer, not a replacement).
    """

    def __init__(self):
        self.player_snapshots: dict[str, PlayerSnapshot] = {}
        self.last_updated_at: float = 0.0

    def record_turn(self, players: list[BasePlayer]) -> None:
        for p in players:
            self.player_snapshots[p.username] = PlayerSnapshot(
                username=p.username,
                is_bot=not isinstance(p, (CLIPlayer, NetworkPlayer)),
                money_cards=[c.value for c in p.money_cards],
                status_cards=[summarize_card(c) for c in p.status_cards],
                current_money_card_bids=[c.value for c in p.current_money_card_bids],
                points=p.points,
                holds_faux_pas=p.holds_faux_pas,
                faux_pas_discarded=p.has_discarded_card,
                active=p.active,
            )
        self.last_updated_at = time.time()
