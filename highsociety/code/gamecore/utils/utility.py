import json
import os
import logging
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager


_bootstrap_logger = logging.getLogger("bootstrap_logger")
_bootstrap_logger.setLevel(logging.ERROR)
if not _bootstrap_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[CONFIG ERROR] %(message)s")
    handler.setFormatter(formatter)
    _bootstrap_logger.addHandler(handler)

def get_base_path():
    all_config_details = get_all_configurations()
    try:
        return all_config_details.get("base_path").get("abs_root_dir")
    except Exception as e:
        _bootstrap_logger.error(f"Error getting base path: {e}")
        return None


def get_all_configurations() -> dict:
    try:
        gameconfigdir = os.getcwd() + "//highsociety//HSConfig.json"
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
