"""
An embedded bot that picks its bid via Monte Carlo Tree Search (see
highsociety/code/ai/mcts/) instead of a fixed heuristic like GreedyBot/
CappedGreedyBot. Exposed to players only as three tuned difficulty presets
(Easy/Medium/Hard — see DIFFICULTY_PRESETS) rather than as a raw "mcts" bot
type with knobs to configure; the difficulty *is* the bot type, the same way
choosing "greedy" already hides GreedyBot's own fixed behavior.

How real, hidden game state becomes one fully-known SimState (see
mcts/simulation.py for what's exact vs. approximated once it's built):
this class listens to everything PlayGame.send_message()s it and combines
that with get_auction_history() (a live, mode-independent reference PlayGame
wires up in its own __init__, unlike broadcasts — see BOT_API.md) to
reconstruct: this bot's own hand exactly (trivial — it's just its own
properties), every other player's remaining hand exactly (every money card
they've ever permanently spent is recorded in auction history; nothing else
about a hand can change), and every player's status cards/points exactly
(who won which auctioned card is also in history). The one thing nothing
makes exactly knowable is the future status card draw order — that's what
actually gets *sampled* (see mcts/simulation.py's sample_future_deck),
repeated across several independent determinizations per decision (see
MCTSConfig.determinizations) and combined, rather than assumed away.
"""
import random
import re
import time
from typing import Optional, Union

from highsociety.code.ai.mcts.policy import capped_greedy_policy
from highsociety.code.ai.mcts.search import MCTSConfig, run_search
from highsociety.code.ai.mcts.simulation import SimCard, SimPlayer, SimState, make_card, sample_future_deck
from highsociety.code.common.utils.utility import get_game_setting_configurations
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.player.player import BasePlayer

# Same regexes GreedyBot/CappedGreedyBot already rely on for the same reason:
# there's no structured "current highest bid"/"card up for auction" accessor
# on BasePlayer, only these human-readable PLAYER_INFO lines gameplay.py
# sends directly to the acting player every turn (see
# _handle_player_turn) — direct player.send_message() calls, so unlike
# AUCTION_UPDATE broadcasts these arrive identically in 'cli' and 'network'
# mode.
_HIGHEST_BID_RE = re.compile(r"Current Highest Bid:\s*(\d+)")
_AUCTIONED_CARD_RE = re.compile(r"Auctioning:\s*(\w+)\s*\(value=(-?\d+)\)")
# _handle_player_turn also sends this direct message to every *other*
# player on someone's turn (again bypassing self.host, so mode-independent)
# — the only way to learn an opponent's username before the very first
# auction has concluded (see _known_opponent_usernames).
_TURN_RE = re.compile(r"^(.+?)'s turn\. Player is playing\.\.$")

# A dedicated Random instance for determinization sampling, rather than the
# `random` module's shared global state — gameplay.py calls random.seed(...)
# for reproducible games (see PlayGame.__init__), and drawing from the same
# global generator here would both consume from that sequence (breaking
# reproducibility for tests/replays) and make this bot's own sampling
# accidentally deterministic across a whole seeded game instead of
# independently random each decision.
_process_rng = random.Random()

_full_starting_values_cache: Optional[frozenset] = None


def _full_starting_values() -> frozenset:
    global _full_starting_values_cache
    if _full_starting_values_cache is None:
        _full_starting_values_cache = frozenset(get_game_setting_configurations()["starting_cash_values"])
    return _full_starting_values_cache


class MCTSBot(BasePlayer):
    def __init__(self, name: str, username: str, config: MCTSConfig, think_time: float = 0) -> None:
        """
        think_time: seconds to pause before returning a decision from
        get_bid(), same meaning as every other bot's — purely for a human-
        watchable simulation; the search itself has no use for it (a fresh
        SimState-based search is fast enough that "how long to think" is
        entirely a UX knob, not a real compute budget).
        """
        super().__init__(name, username)
        self.active = True
        self._config = config
        self._think_time = think_time
        self._current_max_bid = 0
        self._current_card_type: Optional[str] = None
        self._current_card_value: Optional[int] = None
        # Only ever needed before the first auction has concluded — see
        # _known_opponent_usernames' docstring for why this can be
        # incomplete, and why that's fine.
        self._overheard_usernames: set = set()

    def send_message(self, message: str, message_type: str = None, created_at: float = None, **kwargs) -> None:
        if message_type == "GLOBAL_MOVE_INFO":
            match = _TURN_RE.match(message)
            if match:
                self._overheard_usernames.add(match.group(1))
            return
        if message_type != "PLAYER_INFO":
            return
        bid_match = _HIGHEST_BID_RE.search(message)
        if bid_match:
            self._current_max_bid = int(bid_match.group(1))
        card_match = _AUCTIONED_CARD_RE.search(message)
        if card_match:
            self._current_card_type = card_match.group(1)
            self._current_card_value = int(card_match.group(2))

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        time.sleep(self._think_time)
        state, me_idx = self._build_state()
        action = run_search(state, me_idx, capped_greedy_policy, self._config)
        return "pass" if action == "pass" else [action]

    def choose_painting_to_discard(self) -> Optional[Painting]:
        # Not MCTS-driven, deliberately — a Faux Pas discard is comparatively
        # low-stakes next to a bid decision (see mcts/simulation.py's
        # rollout policy for the same reasoning applied to opponents during
        # search), and "give up your cheapest painting" is already close to
        # optimal, matching every existing bot's identical choice.
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return min(paintings, key=lambda c: c.value) if paintings else None

    def _known_opponent_usernames(self) -> list[str]:
        """
        Once at least one auction has concluded, its cards_spent dict
        (see AuctionRecord) covers literally every player in the game,
        participant or not — from that point on, this is the complete,
        exact roster, every time. Before that (we're mid-decision somewhere
        in auction #1), fall back to whoever's turn-order narration we've
        already overheard (see send_message's GLOBAL_MOVE_INFO handling) —
        necessarily incomplete if we're early in turn order (worst case:
        we're the very first to act in the whole game and know literally
        nobody yet), but self-corrects completely the moment auction #1
        concludes, a few decisions later at most.
        """
        history = self.get_auction_history()
        if history:
            return sorted(set(history[-1]["cards_spent"].keys()) - {self.username})
        known = sorted(self._overheard_usernames - {self.username})
        return known or ["?opponent"]  # never model a completely solo auction

    def _build_state(self) -> tuple[SimState, int]:
        history = self.get_auction_history()
        full_values = _full_starting_values()
        quit_usernames = {
            event["player"] for record in history for event in record["events"] if event["action"] == "quit"
        }
        green_count = sum(1 for record in history if record["card"]["is_green"])

        def remaining_hand(username: str) -> list[int]:
            spent: set = set()
            for record in history:
                spent |= set(record["cards_spent"].get(username, []))
            return sorted(full_values - spent)

        def won_status_cards(username: str) -> list[SimCard]:
            cards = [
                SimCard(kind=record["card"]["type"], value=record["card"]["value"],
                        multiplier=record["card"]["multiplier"], is_green=record["card"]["is_green"])
                for record in history if record["recipient"] == username
            ]
            return sorted(cards, key=lambda c: c.value)

        me = SimPlayer(
            username=self.username,
            money_cards=sorted(c.value for c in self.money_cards),
            status_cards=sorted(
                (SimCard(kind=type(c).__name__, value=c.value, multiplier=c.multiplier, is_green=c.is_green)
                 for c in self.status_cards),
                key=lambda c: c.value,
            ),
            current_bid_cards=[c.value for c in self.current_money_card_bids],
            active=True,
        )
        opponents = [
            SimPlayer(username=u, money_cards=remaining_hand(u), status_cards=won_status_cards(u),
                      current_bid_cards=[], active=u not in quit_usernames)
            for u in self._known_opponent_usernames()
        ]
        players = [me] + opponents
        me_idx = 0

        revealed = [
            SimCard(kind=record["card"]["type"], value=record["card"]["value"],
                    multiplier=record["card"]["multiplier"], is_green=record["card"]["is_green"])
            for record in history
        ]
        current_card = make_card(self._current_card_type, value=self._current_card_value)
        revealed.append(current_card)
        if current_card.is_green:
            green_count += 1

        deck = sample_future_deck(revealed, rng=_process_rng)

        state = SimState(
            players=players,
            turn=me_idx,
            deck=deck,
            current_card=current_card,
            is_disgrace=self._current_card_type in {"FauxPas", "Passe", "Scandale"},
            max_bid=self._current_max_bid,
            still_in=[True] * len(players),
            green_count=green_count,
            # Only tracked for our own seat — see the module docstring's
            # "what's approximated" section for why an opponent's pending
            # Faux Pas obligation isn't modeled.
            faux_pas_holder=me_idx if (self.holds_faux_pas and not self.has_discarded_card) else None,
        )
        return state, me_idx


DIFFICULTY_PRESETS = {
    # Tuned by feel, not measurement — bot_evaluator.py (see repo root) is
    # the intended tool for actually comparing these against each other and
    # adjusting the numbers.
    "easy": MCTSConfig(iterations=20, determinizations=2, exploration_constant=1.4),
    "medium": MCTSConfig(iterations=120, determinizations=5, exploration_constant=1.4),
    "hard": MCTSConfig(iterations=500, determinizations=8, exploration_constant=1.0),
}


class EasyMCTSBot(MCTSBot):
    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        super().__init__(name, username, config=DIFFICULTY_PRESETS["easy"], think_time=think_time)


class MediumMCTSBot(MCTSBot):
    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        super().__init__(name, username, config=DIFFICULTY_PRESETS["medium"], think_time=think_time)


class HardMCTSBot(MCTSBot):
    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        super().__init__(name, username, config=DIFFICULTY_PRESETS["hard"], think_time=think_time)
