"""
The MCTS decision logic (see mcts_bot.py for the module docstring's fuller
explanation of what's exact vs. approximated) as pure functions of explicit
inputs, instead of a bot's own accumulated instance state — this is what
lets a decision be made without any per-game object living for the whole
game: given the same (auction_history, event_log, live_state, username,
config, rng), the result doesn't depend on anything else.

auction_history: an AuctionHistory object (see
game_manager/auction_history.py) — every player's current money cards,
status cards held, and Faux Pas status, refreshed after every turn. This is
what a replay-based reconstruction used to have to rebuild from event_log
each call; reading it directly is both simpler and more accurate (it now
covers every status card kind, not just Paintings, and every player's Faux
Pas obligation, not just "me"'s — see decide_bid's faux_pas_holder logic).

event_log: PlayGame.get_auction_history()'s event-level list — still needed
for the one thing AuctionHistory doesn't track: the exact multiset of every
card revealed so far (for sample_future_deck) and the running green-card
count, both counted across every completed auction rather than per-player.

live_state: PlayGame.get_live_auction_state()'s dict — the current card up
for auction and the current highest bid.
"""
import random
from typing import Optional, Union

from highsociety.code.ai.mcts.policy import capped_greedy_policy
from highsociety.code.ai.mcts.search import MCTSConfig, run_search
from highsociety.code.ai.mcts.simulation import SimCard, SimPlayer, SimState, make_card, sample_future_deck
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory

_DISGRACE_KINDS = frozenset({"FauxPas", "Passe", "Scandale"})


def _to_sim_cards(status_cards: list[dict]) -> list[SimCard]:
    return sorted(
        (SimCard(kind=c["type"], value=c["value"], multiplier=c["multiplier"], is_green=c["is_green"])
         for c in status_cards),
        key=lambda c: c.value,
    )


def decide_bid(auction_history: AuctionHistory, event_log: list[dict], live_state: dict,
                username: str, config: MCTSConfig, rng: random.Random) -> Union[list[int], str]:
    my_snapshot = auction_history.player_snapshots[username]
    green_count = sum(1 for record in event_log if record["card"]["is_green"])

    me = SimPlayer(
        username=username,
        money_cards=sorted(my_snapshot.money_cards),
        status_cards=_to_sim_cards(my_snapshot.status_cards),
        current_bid_cards=list(my_snapshot.current_money_card_bids),
        active=my_snapshot.active,
    )
    opponents = [
        SimPlayer(username=snap.username, money_cards=sorted(snap.money_cards),
                  status_cards=_to_sim_cards(snap.status_cards), current_bid_cards=[],
                  active=snap.active)
        for snap in auction_history.player_snapshots.values() if snap.username != username
    ]
    players = [me] + opponents
    me_idx = 0

    revealed = [
        SimCard(kind=record["card"]["type"], value=record["card"]["value"],
                multiplier=record["card"]["multiplier"], is_green=record["card"]["is_green"])
        for record in event_log
    ]
    current_card_info = live_state["card"]
    current_card = make_card(current_card_info["type"], value=current_card_info["value"])
    revealed.append(current_card)
    if current_card.is_green:
        green_count += 1

    deck = sample_future_deck(revealed, rng=rng)

    # Tracked for EVERY player now, not just "me" -- PlayerSnapshot.
    # holds_faux_pas/faux_pas_discarded is exact for everyone, fixing an
    # approximation the old replay-based version had to accept (see
    # simulation.py's module docstring).
    faux_pas_holder = next(
        (i for i, p in enumerate(players)
         if auction_history.player_snapshots[p.username].holds_faux_pas
         and not auction_history.player_snapshots[p.username].faux_pas_discarded),
        None,
    )

    state = SimState(
        players=players,
        turn=me_idx,
        deck=deck,
        current_card=current_card,
        is_disgrace=current_card_info["type"] in _DISGRACE_KINDS,
        max_bid=live_state["max_bid"],
        still_in=[True] * len(players),
        green_count=green_count,
        faux_pas_holder=faux_pas_holder,
    )

    action = run_search(state, me_idx, capped_greedy_policy, config)
    return "pass" if action == "pass" else [action]


def decide_faux_pas_discard(my_status_cards: list[dict]) -> Optional[int]:
    """
    my_status_cards: the same summarize_card()-shaped dicts AuctionHistory
    uses (e.g. auction_history.player_snapshots[username].status_cards).
    Returns the VALUE of the painting to discard (not a Painting object --
    a stateless caller doesn't hold real game objects), or None if there's
    nothing to discard. "Give up the cheapest painting" -- not MCTS-driven,
    deliberately (see the original choose_painting_to_discard's reasoning,
    preserved here: a Faux Pas discard is comparatively low-stakes next to
    a bid decision, and this choice is already close to optimal).
    """
    painting_values = [c["value"] for c in my_status_cards if c["type"] == "Painting"]
    return min(painting_values, default=None)
