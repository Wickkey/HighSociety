from highsociety.code.common.utils.utility import validate_player_count


def test_rejects_below_configured_minimum():
    error = validate_player_count(1, {"min_players": 2, "max_players": 5})
    assert error is not None
    assert "at least 2" in error.lower()


def test_rejects_above_configured_maximum():
    error = validate_player_count(20, {"min_players": 2, "max_players": 5})
    assert error is not None
    assert "at most 5" in error.lower()


def test_accepts_a_count_within_range():
    assert validate_player_count(3, {"min_players": 2, "max_players": 5}) is None


def test_accepts_the_exact_boundaries():
    assert validate_player_count(2, {"min_players": 2, "max_players": 5}) is None
    assert validate_player_count(5, {"min_players": 2, "max_players": 5}) is None


def test_defaults_min_players_to_2_when_missing_from_rules():
    assert validate_player_count(1, {}) is not None
    assert validate_player_count(2, {}) is None


def test_no_upper_bound_when_max_players_missing_from_rules():
    assert validate_player_count(1000, {"min_players": 2}) is None


def test_uses_real_config_when_rules_not_given(monkeypatch):
    import highsociety.code.common.utils.utility as utility_module
    monkeypatch.setattr(utility_module, "get_game_setting_configurations", lambda: {"min_players": 4, "max_players": 4})
    assert validate_player_count(4) is None
    assert validate_player_count(3) is not None
