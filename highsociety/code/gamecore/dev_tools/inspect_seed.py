#!/usr/bin/env python3
"""
Dev tool: shows the exact, deterministic card draw order a given RNG seed
produces (StatusCardManager's shuffle is the first thing PlayGame.__init__
does after seeding, so this matches what a real seeded PlayGame will draw).

Use this to plan ScriptedPlayer test decisions against a *known* sequence of
auctions — e.g. "card #3 is FauxPas, so script player B to quit right there
to hit the quit-during-disgrace-auction path" — instead of guessing blindly.

Usage:
    python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --seed 42
    python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --seed 42 --save my_scenario
    python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --list
"""

import argparse
import json
import random
from pathlib import Path

from highsociety.code.gamecore.card_manager.status_card_manager import StatusCardManager
from highsociety.code.common.utils.utility import get_game_setting_configurations

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "gamecore" / "unittest" / "scenarios"


def draw_order_for_seed(seed: int) -> list:
    """Returns the full deck in draw order for the given seed, as repr strings."""
    random.seed(seed)
    manager = StatusCardManager()
    order = []
    while not manager.is_empty():
        order.append(repr(manager.remove_top_card()))
    return order


def find_green_cutoff(order: list) -> int:
    """
    Index of the card at which the green-card limit is hit (that card is
    drawn but never auctioned — see gameplay.py's _should_end_game). Returns
    len(order) if the limit is never reached (shouldn't happen with default
    config, but config-dependent).
    """
    limit = get_game_setting_configurations().get("green_card_limit", 4)
    green_seen = 0
    for i, card_repr in enumerate(order):
        if "color=green" in card_repr:
            green_seen += 1
            if green_seen >= limit:
                return i
    return len(order)


def print_order(seed: int, order: list, cutoff: int):
    print(f"Seed {seed} — {len(order)} cards total, green-card cutoff at index {cutoff}\n")
    for i, card_repr in enumerate(order):
        marker = ""
        if i == cutoff:
            marker = "  <-- game ends here (4th green card drawn, not auctioned)"
        elif i > cutoff:
            marker = "  (never reached)"
        print(f"  [{i:2d}] {card_repr}{marker}")


def save_scenario(name: str, seed: int, order: list, cutoff: int, description: str = ""):
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCENARIOS_DIR / f"{name}.json"
    payload = {
        "name": name,
        "seed": seed,
        "description": description,
        "card_order": order,
        "green_cutoff_index": cutoff,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nSaved scenario to {path}")


def load_scenario(name: str) -> dict:
    path = SCENARIOS_DIR / f"{name}.json"
    with open(path, "r") as f:
        return json.load(f)


def list_scenarios():
    if not SCENARIOS_DIR.exists():
        print("No scenarios saved yet.")
        return
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        print(f"  {data['name']:<25} seed={data['seed']:<8} {data.get('description', '')}")


def main():
    parser = argparse.ArgumentParser(description="Inspect the deterministic card order for a seed")
    parser.add_argument("--seed", type=int, help="Seed to inspect")
    parser.add_argument("--save", type=str, metavar="NAME", help="Save this seed's card order as a named scenario")
    parser.add_argument("--description", type=str, default="", help="Optional description to store with --save")
    parser.add_argument("--list", action="store_true", help="List previously saved scenarios")
    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    if args.seed is None:
        parser.error("--seed is required unless --list is given")

    order = draw_order_for_seed(args.seed)
    cutoff = find_green_cutoff(order)
    print_order(args.seed, order, cutoff)

    if args.save:
        save_scenario(args.save, args.seed, order, cutoff, args.description)


if __name__ == "__main__":
    main()
