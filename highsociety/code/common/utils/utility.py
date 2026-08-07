import base64
import json
import os
import logging
import uuid
from pathlib import Path
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager


_bootstrap_logger = logging.getLogger("bootstrap_logger")
_bootstrap_logger.setLevel(logging.ERROR)
if not _bootstrap_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[CONFIG ERROR] %(message)s")
    handler.setFormatter(formatter)
    _bootstrap_logger.addHandler(handler)

# .../<repo_root>/highsociety/code/common/utils/utility.py -> <repo_root>
_REPO_ROOT = Path(__file__).resolve().parents[4]

def get_base_path():
    """
    Returns HSConfig.json's configured abs_root_dir if it exists on this
    machine, otherwise falls back to the repo root computed from this file's
    own location. The configured value is typically someone's personal
    dev-machine path (see HSConfig.json), which would otherwise break on any
    other machine — another developer's checkout, or a hosting provider's
    container.
    """
    all_config_details = get_all_configurations()
    try:
        configured = all_config_details.get("base_path").get("abs_root_dir")
        if configured and os.path.isdir(configured):
            return configured
    except Exception as e:
        _bootstrap_logger.error(f"Error getting base path: {e}")
    return str(_REPO_ROOT)


def get_all_configurations() -> dict:
    try:
        gameconfigdir = _REPO_ROOT / "highsociety" / "HSConfig.json"
        with open(gameconfigdir, "r") as f:
            data = json.load(f)

        return data

    except Exception as e:
        _bootstrap_logger.error(f"Error getting base path: {e}")
        return None
    

def get_logging_configurations():
    all_config_details = get_all_configurations()
    try:
        logging_details = all_config_details.get("logging")
    except Exception as e:
        _bootstrap_logger.error(f"Error getting base path: {e}")
        return None

    return logging_details


def get_game_setting_configurations():
    all_config_details = get_all_configurations()
    try:
        return all_config_details["game_settings"]["rules"]
    except Exception as e:
        _bootstrap_logger.error(f"Error getting base path: {e}")
        return None


def get_game_metadata_configurations():
    all_config_details = get_all_configurations()
    try:
        return all_config_details["game_settings"]["metadata"]
    except Exception as e:
        _bootstrap_logger.error(f"Error getting base path: {e}")
        return None


def validate_player_count(num_players: int, rules: dict = None) -> str:
    """
    Checks num_players against HSConfig.json's min_players/max_players (a
    dict from get_game_setting_configurations(), fetched automatically if
    not given). Returns an error message string if out of range, or None if
    valid — shared by main.py and network_server.py so both entry points
    enforce the exact same rule instead of each hardcoding their own.
    """
    rules = rules if rules is not None else (get_game_setting_configurations() or {})
    min_players = rules.get("min_players", 2)
    max_players = rules.get("max_players")

    if num_players < min_players:
        return f"At least {min_players} players are required."
    if max_players is not None and num_players > max_players:
        return f"At most {max_players} players are allowed."
    return None


def generate_game_id() -> str:
    """
    A short, URL/filename-safe random id for one game session — used as the
    `game_id` every player/spectator message carries (see network/protocol.py)
    so a stale or misdirected message can be told apart from this game's own.
    Shared by network_server.py and web_server.py so both entry points mint
    ids the same way.
    """
    raw = uuid.uuid4().bytes
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
