"""
A small, self-contained re-implementation of High Society's auction rules,
operating on plain dataclasses instead of the real BasePlayer/PlayGame
objects. This is what MCTSBot (see ../mcts_bot.py) actually searches over:
running the *real* engine (with its sockets/threads/pacing sleeps) tens of
thousands of times per decision isn't practical, so this reproduces the same
rules -- highest-bid-wins normal auctions, first-to-pass-loses disgrace
auctions, Faux Pas discard obligations, money-elimination + points scoring --
as a fast, pure function of a `SimState`, matching
highsociety/code/gamecore/game_manager/gameplay.py's logic step for step.

What's exact vs. approximated, and why (see mcts_bot.py's module docstring
for how the live game state gets turned into a SimState in the first place):

- Every player's own hand/status cards/points are exact, always -- an
  auction's `cards_spent` (see AuctionRecord in
  game_manager/auction_information.py) permanently removes specific card
  VALUES from a player's hand the moment it's revealed, and money cards
  never come back once spent. Summing `cards_spent` across every completed
  auction (available via get_auction_history(), which is a direct list
  reference PlayGame wires up -- unlike broadcasts, this works identically
  whether the real game is running in 'cli' or 'network' mode) against the
  known starting hand therefore gives an *exact* remaining hand for every
  player, not a probabilistic guess.
- The only genuinely unknown piece of the real game state is the future
  status card draw order -- sample_future_deck() below samples one
  plausible ordering of whatever's left, consistent with what's already
  been revealed. Re-sampling this deck order for several independent
  determinizations (see search.py) is what actually makes this an
  imperfect-information-aware search rather than assuming perfect
  information.
- Approximation: an opponent's money committed to the *currently in
  progress* auction (as opposed to money already permanently spent in a
  *completed* one) isn't modeled separately -- their whole known remaining
  hand is treated as available to raise with. This slightly overestimates
  how much they could still add this specific auction if they've already
  bid something this round, in exchange for not needing mid-auction
  structured broadcasts (AUCTION_UPDATE's `cards=` field), which are only
  ever delivered in 'network' mode -- CLI-mode games (bot_evaluator.py,
  main.py) would otherwise see this bot behave inconsistently by host type.
- Approximation: a Faux Pas discard obligation is only tracked for this
  bot's own seat (BasePlayer.holds_faux_pas/has_discarded_card are exact
  for "me"). An *opponent* holding an unresolved Faux Pas is not detected
  from history (the discard event itself isn't recorded in
  get_auction_history() — only auction wins/losses are), so an opponent's
  simulated future never forces a discard on their behalf. Bounded,
  rare impact: at most one card's worth of points overestimated for one
  opponent, only in games where they're currently sitting on an
  undischarged Faux Pas.
"""
import random
from dataclasses import dataclass, field, replace
from typing import Optional

from highsociety.code.common.utils.utility import get_game_setting_configurations

# Every kind of status card that's "green" (counts toward the 4-green-card
# game-ending limit) and its fixed (multiplier, is_green) attributes --
# matches highsociety/code/gamecore/components_module/{painting,
# prestige_card,disgrace_card}.py exactly. Painting's value varies per card
# (that's the whole point of the card); every other kind has one fixed value
# regardless of which specific instance it is.
_CARD_ATTRS = {
    "Painting": {"multiplier": 1, "is_green": False},
    "PrestigeCard": {"multiplier": 2, "is_green": True, "value": 0},
    "FauxPas": {"multiplier": 1, "is_green": False, "value": 0},
    "Passe": {"multiplier": 1, "is_green": False, "value": -5},
    "Scandale": {"multiplier": 0.5, "is_green": True, "value": 0},
}
_DISGRACE_KINDS = frozenset({"FauxPas", "Passe", "Scandale"})

# HSConfig.json's disgrace_card_types entries -> the SimCard "kind" name
# (matches CardFactory's lowercase keys to the real classes' names).
_DISGRACE_TYPE_NAMES = {"faux_pas": "FauxPas", "passe": "Passe", "scandale": "Scandale"}


@dataclass(frozen=True)
class SimCard:
    kind: str          # "Painting" | "PrestigeCard" | "FauxPas" | "Passe" | "Scandale"
    value: int
    multiplier: float
    is_green: bool


def make_card(kind: str, value: Optional[int] = None) -> SimCard:
    """Builds a SimCard from just a kind (+ value, for Painting only) --
    every other attribute is implied by the kind. Mirrors CardFactory, but
    for the plain-data SimCard shape instead of a real StatusCard object."""
    attrs = _CARD_ATTRS[kind]
    return SimCard(kind=kind, value=attrs.get("value", value), multiplier=attrs["multiplier"], is_green=attrs["is_green"])


_full_deck_composition_cache: Optional[list[SimCard]] = None


def full_deck_composition() -> list[SimCard]:
    """Every status card that exists in a fresh game, per HSConfig.json --
    the starting point sample_future_deck() below subtracts revealed cards
    from. Cached at module level (config is static for the process's
    lifetime) since this would otherwise re-read HSConfig.json's file from
    disk once per determinization, of which a single bot decision can
    trigger a couple dozen (see mcts_bot.py's DIFFICULTY_PRESETS). Always
    returns a fresh list -- callers (sample_future_deck) mutate it."""
    global _full_deck_composition_cache
    if _full_deck_composition_cache is None:
        config = get_game_setting_configurations()
        cards = [make_card("Painting", value=v) for v in config.get("painting_values", [])]
        cards += [make_card("PrestigeCard") for _ in range(config.get("prestige_card_count", 0))]
        cards += [make_card(_DISGRACE_TYPE_NAMES[t]) for t in config.get("disgrace_card_types", [])
                  if t in _DISGRACE_TYPE_NAMES]
        _full_deck_composition_cache = cards
    return list(_full_deck_composition_cache)


def sample_future_deck(revealed: list[SimCard], rng: random.Random) -> list[SimCard]:
    """One plausible random ordering of whatever status cards haven't been
    revealed yet (via a completed auction, or the one currently up for
    auction), consistent with the full known deck composition. This is the
    *only* thing about the real game this module treats as genuinely
    unknown -- see the module docstring."""
    remaining = full_deck_composition()
    for card in revealed:
        remaining.remove(card)  # removes one exact match; raises if double-counted, which would be a caller bug
    rng.shuffle(remaining)
    return remaining


@dataclass
class SimPlayer:
    username: str
    money_cards: list[int] = field(default_factory=list)   # sorted, uncommitted-to-any-auction values
    status_cards: list[SimCard] = field(default_factory=list)
    current_bid_cards: list[int] = field(default_factory=list)  # committed to the CURRENT auction only
    active: bool = True

    @property
    def current_bid(self) -> int:
        return sum(self.current_bid_cards)


@dataclass
class SimState:
    """
    Everything needed to keep simulating a game forward from "right now,
    it's players[turn]'s decision." Deliberately plain/copyable (no object
    references back into the real game) so a tree search can hold many of
    these at once cheaply (see search.py).
    """
    players: list[SimPlayer]
    turn: int                              # index into players
    deck: list[SimCard]                    # future draws; index 0 = next
    current_card: SimCard
    is_disgrace: bool
    max_bid: int
    still_in: list[bool]                   # parallel to players; contesting the CURRENT auction
    green_count: int
    faux_pas_holder: Optional[int] = None  # index into players, or None
    settlement: str = "forfeit"            # "forfeit" | "refund_all" -- see disgrace_settlement.py
    green_card_limit: int = 4
    game_over: bool = False

    def clone(self) -> "SimState":
        """A real (not shallow) copy -- every list/sub-dataclass duplicated
        -- since step() mutates in place for simplicity and callers (the
        tree search) need independent branches."""
        return SimState(
            players=[SimPlayer(p.username, list(p.money_cards), list(p.status_cards),
                                list(p.current_bid_cards), p.active) for p in self.players],
            turn=self.turn, deck=list(self.deck), current_card=self.current_card,
            is_disgrace=self.is_disgrace, max_bid=self.max_bid, still_in=list(self.still_in),
            green_count=self.green_count, faux_pas_holder=self.faux_pas_holder,
            settlement=self.settlement, green_card_limit=self.green_card_limit,
            game_over=self.game_over,
        )


def legal_actions(state: SimState) -> list:
    """"pass", plus one action per money-card VALUE the current player could
    add on its own to strictly exceed the current max bid. Deliberately
    single-card raises only (never a combination of several cards at once)
    -- the same simplification GreedyBot/CappedGreedyBot already make, kept
    here so the action space stays small enough to actually search
    (11 cards -> at most 12 branches per decision, not 2^11)."""
    if state.game_over:
        return []
    player = state.players[state.turn]
    actions = ["pass"]
    actions += [v for v in player.money_cards if player.current_bid + v > state.max_bid]
    return actions


def is_terminal(state: SimState) -> bool:
    return state.game_over


def _next_turn(state: SimState, require_still_in: bool) -> Optional[int]:
    """The next player index who's active (and, for a normal auction,
    still_in) after state.turn -- None if nobody qualifies (shouldn't
    happen; callers only ask once they know >=1 such player exists)."""
    n = len(state.players)
    for step in range(1, n + 1):
        idx = (state.turn + step) % n
        p = state.players[idx]
        if p.active and (not require_still_in or state.still_in[idx]):
            return idx
    return None


def _count_active(state: SimState) -> int:
    return sum(1 for p in state.players if p.active)


def _resolve_faux_pas_if_owed(state: SimState) -> None:
    """Mirrors gameplay.py's play_game(): right after any auction concludes,
    if someone's still sitting on an unresolved Faux Pas, check whether they
    now hold a Painting to discard (heuristic: always the cheapest one, same
    as every existing bot's choose_painting_to_discard) -- otherwise the
    obligation just carries over to the next check."""
    if state.faux_pas_holder is None:
        return
    holder = state.players[state.faux_pas_holder]
    paintings = [c for c in holder.status_cards if c.kind == "Painting"]
    if not paintings:
        return
    cheapest = min(paintings, key=lambda c: c.value)
    holder.status_cards.remove(cheapest)
    state.faux_pas_holder = None


def _draw_next_card(state: SimState, next_starting_player: int) -> None:
    """Shared tail end of finishing an auction: check the green-card/empty-
    deck end conditions, then either set up the next auction or end the
    game -- matches play_game()'s main while-loop body exactly (draw, check
    green count against the limit, check <2 active players, else start the
    next auction)."""
    _resolve_faux_pas_if_owed(state)

    if _count_active(state) < 2:
        state.game_over = True
        return
    if not state.deck:
        state.game_over = True
        return

    card = state.deck.pop(0)
    if card.is_green:
        state.green_count += 1
        if state.green_count >= state.green_card_limit:
            # The limit-th green card ends the game immediately, without
            # ever going to auction -- see play_game()'s is_final_green_card
            # branch.
            state.game_over = True
            return

    state.current_card = card
    state.is_disgrace = card.kind in _DISGRACE_KINDS
    state.max_bid = 0
    # Freshly reset, so any active player is automatically still_in too --
    # no separate "and still_in" check needed for next_starting_player itself.
    state.still_in = [p.active for p in state.players]
    if state.players[next_starting_player].active:
        state.turn = next_starting_player
    else:
        # The real starting player is gone (inactive) -- fall back to
        # whoever's next in seat order, same as get_next_player_id would
        # naturally land on for a live game.
        state.turn = next_starting_player
        state.turn = _next_turn(state, require_still_in=True)


def _finalize_normal_auction(state: SimState) -> None:
    winner_idx = next((i for i, p in enumerate(state.players) if state.still_in[i] and p.active), None)
    if winner_idx is not None:
        winner = state.players[winner_idx]
        winner.status_cards.append(state.current_card)
        winner.status_cards.sort(key=lambda c: c.value)
        winner.current_bid_cards = []  # paid for good -- never refunded
        if state.current_card.kind == "FauxPas":
            state.faux_pas_holder = winner_idx
    _draw_next_card(state, next_starting_player=winner_idx if winner_idx is not None else state.turn)


def _finalize_disgrace_auction(state: SimState, loser_idx: int) -> None:
    loser = state.players[loser_idx]
    loser.status_cards.append(state.current_card)
    loser.status_cards.sort(key=lambda c: c.value)
    if state.current_card.kind == "FauxPas":
        state.faux_pas_holder = loser_idx

    if state.settlement == "refund_all":
        for p in state.players:
            p.money_cards.extend(p.current_bid_cards)
            p.money_cards.sort()
            p.current_bid_cards = []
    else:  # "forfeit" (default, see ForfeitSettlement) -- raisers' cards are simply never returned
        for i, p in enumerate(state.players):
            if i != loser_idx:
                p.current_bid_cards = []

    _draw_next_card(state, next_starting_player=loser_idx)


def _step_mut(state: SimState, action) -> None:
    """The actual rule engine — mutates `state` in place to apply exactly
    one action for state.players[state.turn]. Handles auction resolution /
    Faux Pas / drawing the next card / ending the game internally, exactly
    as gameplay.py's normal_card_auction / disgrace_card_auction /
    play_game do. Not exported: every external caller goes through step()
    (a single clone + this) or, for a multi-step run (a rollout, or
    fast-forwarding through opponents' turns), clones once up front and
    calls this repeatedly — see advance_to_actor/rollout_to_end below. A
    single MCTS search can call this thousands of times per real decision
    (config.iterations x config.determinizations rollouts, each
    potentially dozens of steps), so avoiding a full deep-copy on every
    individual step (a tree search *does* still need one per node, via
    step() itself) is what keeps a whole search fast enough to run inside
    one bot turn instead of taking several seconds."""
    player = state.players[state.turn]

    if action == "pass":
        player.money_cards.extend(player.current_bid_cards)
        player.money_cards.sort()
        player.current_bid_cards = []
        state.still_in[state.turn] = False

        if state.is_disgrace:
            _finalize_disgrace_auction(state, loser_idx=state.turn)
            return

        remaining = sum(1 for i, p in enumerate(state.players) if state.still_in[i] and p.active)
        if remaining <= 1:
            _finalize_normal_auction(state)
            return
        state.turn = _next_turn(state, require_still_in=True)
        return

    # A numeric raise -- legal_actions() only ever offers values that are
    # actually in the player's hand and that strictly exceed the current
    # max bid once added.
    player.money_cards.remove(action)
    player.current_bid_cards.append(action)
    state.max_bid = player.current_bid

    if state.is_disgrace:
        state.turn = _next_turn(state, require_still_in=False)
    else:
        state.turn = _next_turn(state, require_still_in=True)


def step(state: SimState, action) -> SimState:
    """Applies exactly one action for state.players[state.turn] and returns
    the resulting state as a fresh clone -- the input is never mutated.
    The right choice whenever the caller needs the *input* state to stay
    valid/reusable afterward (a tree search branching multiple ways from
    the same node) — see _step_mut's docstring for the multi-step,
    single-clone alternative used internally by rollouts."""
    state = state.clone()
    _step_mut(state, action)
    return state


def advance_to_actor(state: SimState, actor_idx: int, policy) -> SimState:
    """Repeatedly applies `policy(state)` for whoever's turn it currently
    is, until either it's genuinely actor_idx's turn again or the game has
    ended -- used to fast-forward through every *other* player's decisions
    with a fixed policy, so a tree search only ever branches on actor_idx's
    own choices (see search.py). Safe to call when it's already
    actor_idx's turn (returns immediately, without cloning)."""
    if state.game_over or state.turn == actor_idx:
        return state
    state = state.clone()
    while not state.game_over and state.turn != actor_idx:
        _step_mut(state, policy(state))
    return state


def rollout_to_end(state: SimState, policy) -> SimState:
    """Plays every remaining decision (everyone's, including actor_idx's
    own beyond whatever a tree search has already explored) using `policy`,
    until the game ends. This module has no expensive I/O or real-time
    pacing at all (unlike the real PlayGame), so a full rollout costs a few
    hundred cheap steps at most -- no depth cap/horizon needed."""
    if state.game_over:
        return state
    state = state.clone()
    while not state.game_over:
        _step_mut(state, policy(state))
    return state


def _score(player: SimPlayer) -> float:
    total = sum(c.value for c in player.status_cards)
    multiplier = 1.0
    for c in player.status_cards:
        multiplier *= c.multiplier
    return total * multiplier


def _money_left(player: SimPlayer) -> int:
    return sum(player.money_cards) + sum(player.current_bid_cards)


def evaluate_reward(state: SimState, me_idx: int) -> float:
    """Only meaningful once is_terminal(state) -- replicates
    gameplay.py's determine_winner() exactly (money-elimination among
    active players first, then highest points among the survivors, ties
    split evenly) and returns *this* search's reward for me_idx: 1.0 if
    they're the sole winner, 1/N if tied among N winners, 0.0 otherwise
    (including "eliminated"/inactive)."""
    active_indices = [i for i, p in enumerate(state.players) if p.active]
    if not active_indices:
        return 0.0

    candidates = set(active_indices)
    if len(active_indices) > 1:
        money = {i: _money_left(state.players[i]) for i in active_indices}
        min_money = min(money.values())
        lowest = [i for i in active_indices if money[i] == min_money]
        if len(lowest) == 1:
            candidates.discard(lowest[0])

    if not candidates:
        return 0.0
    points = {i: _score(state.players[i]) for i in candidates}
    max_points = max(points.values())
    winners = [i for i in candidates if points[i] == max_points]
    if me_idx not in winners:
        return 0.0
    return 1.0 / len(winners)
