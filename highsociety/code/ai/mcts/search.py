"""
The actual Monte Carlo Tree Search: given one determinized SimState (see
simulation.py) where it's `me_idx`'s turn, explore the tree of *my* possible
actions (opponents' turns in between are auto-resolved by a fixed policy,
not branched on -- see simulation.py's advance_to_actor) and return the most
promising one.

Standard UCT (Upper Confidence bound applied to Trees):
  1. Selection   -- walk down the tree from the root, at each fully-expanded
                     node picking the child that maximizes UCB1
                     (exploitation + an exploration bonus for
                     under-visited children), until reaching a node with
                     an untried action or a terminal state.
  2. Expansion   -- if the reached node has an untried action, take it and
                     add the resulting state as a new child node.
  3. Simulation  -- from that new node, play the rest of the game out with
                     a fast heuristic policy for everyone (rollout_to_end)
                     and score the outcome.
  4. Backpropagation -- add that outcome to every node's running
                     visit-count/total-reward along the path back to the
                     root.

Since the *only* thing genuinely random about the underlying game is the
future card order (see simulation.py's module docstring), a single
determinization only explores one possible deck shuffle. run_search() below
repeats the whole tree search across several independent determinizations
(a fresh shuffled deck each time) and combines them by summing visit counts
per action across all the resulting trees -- the action landing on top most
consistently, across the most independently-sampled futures, wins.
"""
import math
from dataclasses import dataclass

from highsociety.code.ai.mcts.simulation import (
    SimState, advance_to_actor, evaluate_reward, is_terminal, legal_actions, rollout_to_end, step,
)


@dataclass(frozen=True)
class MCTSConfig:
    iterations: int          # tree-search iterations per determinization
    determinizations: int    # independent hidden-deck-order samples to search over and combine
    exploration_constant: float = 1.4  # UCB1's "C" -- higher favors exploring under-visited actions more


class _Node:
    __slots__ = ("state", "action", "parent", "children", "untried_actions", "visits", "total_reward")

    def __init__(self, state: SimState, action=None, parent: "_Node" = None):
        self.state = state
        self.action = action        # the action that produced this node from its parent (None for the root)
        self.parent = parent
        self.children: list["_Node"] = []
        self.untried_actions = [] if is_terminal(state) else legal_actions(state)
        self.visits = 0
        self.total_reward = 0.0

    @property
    def is_fully_expanded(self) -> bool:
        return not self.untried_actions

    def best_child_by_ucb1(self, exploration_constant: float) -> "_Node":
        log_parent_visits = math.log(self.visits)

        def ucb1(child: "_Node") -> float:
            exploitation = child.total_reward / child.visits
            exploration = exploration_constant * math.sqrt(log_parent_visits / child.visits)
            return exploitation + exploration

        return max(self.children, key=ucb1)

    def most_visited_child(self) -> "_Node":
        return max(self.children, key=lambda c: c.visits)


def _run_one_tree(root_state: SimState, me_idx: int, rollout_policy, config: MCTSConfig) -> dict:
    """One full UCT search from a single determinized root. Returns
    {action: visit_count} for the root's children, which run_search()
    combines across determinizations."""
    root = _Node(root_state)

    for _ in range(config.iterations):
        node = root
        state = root.state

        # 1. Selection -- descend while every child at this level has
        # already been tried at least once.
        while node.is_fully_expanded and node.children and not is_terminal(state):
            node = node.best_child_by_ucb1(config.exploration_constant)
            state = node.state

        # 2. Expansion -- try one new action at the node we stopped at.
        if node.untried_actions:
            action = node.untried_actions.pop()
            child_state = advance_to_actor(step(state, action), me_idx, rollout_policy)
            node = _Node(child_state, action=action, parent=node)
            node.parent.children.append(node)
            state = child_state

        # 3. Simulation -- play the rest of the game out fast. step() (used
        # throughout rollout_to_end) always returns a fresh clone rather
        # than mutating its input, so the tree's own stored `state` here is
        # never touched by this.
        reward = evaluate_reward(rollout_to_end(state, rollout_policy), me_idx)

        # 4. Backpropagation.
        backprop_node = node
        while backprop_node is not None:
            backprop_node.visits += 1
            backprop_node.total_reward += reward
            backprop_node = backprop_node.parent

    return {child.action: child.visits for child in root.children}


def run_search(root_state: SimState, me_idx: int, rollout_policy, config: MCTSConfig):
    """The full search: builds `config.determinizations` independent trees
    (each with its own freshly re-shuffled future deck -- see
    simulation.sample_future_deck, called by whoever builds each
    determinized root_state before this) and returns whichever action
    accumulated the most total visits summed across all of them. Actions
    are directly comparable across determinizations because they're drawn
    from this player's own exactly-known hand, which doesn't change between
    determinizations -- only the unseen future deck order does.
    """
    combined: dict = {}
    for _ in range(config.determinizations):
        visits_by_action = _run_one_tree(root_state.clone(), me_idx, rollout_policy, config)
        for action, visits in visits_by_action.items():
            combined[action] = combined.get(action, 0) + visits

    if not combined:
        # No children ever got expanded -- only possible if config.iterations
        # is 0, or the root itself was already terminal (shouldn't happen;
        # a bot only ever searches when it genuinely has a decision to make).
        actions = legal_actions(root_state)
        return actions[0] if actions else "pass"

    return max(combined, key=combined.get)
