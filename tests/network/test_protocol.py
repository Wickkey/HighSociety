import pytest

from highsociety.code.gamecore.network.protocol import build_player_payload, build_spectator_payload


class TestPlayerPayload:
    def test_player_move_includes_constraints_and_requires_response(self):
        payload = build_player_payload(
            game_id="g1", username="alice", message_type="PLAYER_MOVE",
            prompt="bid?", constraints={"allowed_money_cards": [1, 2]},
        )
        assert payload["requires_response"] is True
        assert payload["constraints"] == {"allowed_money_cards": [1, 2]}
        assert payload["player_id"] == ["alice"]
        assert payload["prompt"] == "bid?"

    def test_player_move_defaults_move_type_to_bid(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="PLAYER_MOVE", prompt="bid?")
        assert payload["move_type"] == "bid"

    def test_player_move_respects_an_explicit_move_type(self):
        payload = build_player_payload(
            game_id="g1", username="alice", message_type="PLAYER_MOVE", prompt="discard?",
            move_type="discard_painting",
        )
        assert payload["move_type"] == "discard_painting"

    def test_move_type_is_absent_on_non_player_move_messages(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="PLAYER_INFO", prompt="info")
        assert "move_type" not in payload

    def test_player_info_does_not_require_response_or_include_constraints(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="PLAYER_INFO", prompt="info")
        assert payload["requires_response"] is False
        assert "constraints" not in payload

    def test_global_event_omits_player_id(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="GLOBAL_EVENT", prompt="hi all")
        assert "player_id" not in payload
        assert payload["prompt"] == "hi all"

    def test_unknown_message_type_raises(self):
        with pytest.raises(ValueError):
            build_player_payload(game_id="g1", username="alice", message_type="NOT_REAL", prompt="x")

    def test_created_at_defaults_to_now_if_not_given(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="INFO", prompt="x")
        assert isinstance(payload["created_at"], float)

    def test_created_at_is_respected_when_given(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="INFO", prompt="x", created_at=123.0)
        assert payload["created_at"] == 123.0


class TestSpectatorPayload:
    def test_global_event_shape(self):
        payload = build_spectator_payload(game_id="g1", message_type="GLOBAL_EVENT", prompt="hi")
        assert payload["requires_response"] is False
        assert payload["prompt"] == "hi"

    def test_chat_defaults_from_user_and_scope_when_not_given(self):
        payload = build_spectator_payload(game_id="g1", message_type="CHAT", prompt="gg")
        assert payload["from_user"] == "nan"
        assert payload["to_user(s)"] == "all"

    def test_chat_carries_the_actual_sender_and_scope(self):
        payload = build_spectator_payload(
            game_id="g1", message_type="CHAT", prompt="gg", from_user="alice", to_users="spectators",
        )
        assert payload["from_user"] == "alice"
        assert payload["to_user(s)"] == "spectators"

    def test_unknown_message_type_raises(self):
        with pytest.raises(ValueError):
            build_spectator_payload(game_id="g1", message_type="PLAYER_MOVE", prompt="x")


class TestPlayerChatPayload:
    def test_player_can_receive_a_chat_message(self):
        payload = build_player_payload(
            game_id="g1", username="alice", message_type="CHAT", prompt="hi",
            from_user="bob-the-spectator", to_users="all",
        )
        assert payload["message_type"] == "CHAT"
        assert payload["from_user"] == "bob-the-spectator"
        assert payload["to_user(s)"] == "all"
        assert "player_id" not in payload  # CHAT isn't addressed to a specific player_id


class TestStructuredDataField:
    """data lets any broadcast carry machine-parseable content (e.g. AUCTION_RESULT)
    alongside its human-readable prompt — this is what a bot actually parses."""

    def test_player_payload_omits_data_key_when_not_given(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="GLOBAL_EVENT", prompt="hi")
        assert "data" not in payload

    def test_player_payload_carries_data_verbatim_when_given(self):
        record = {"round_number": 1, "recipient": "alice", "price_paid": 8}
        payload = build_player_payload(
            game_id="g1", username="alice", message_type="AUCTION_RESULT", prompt="result", data=record,
        )
        assert payload["data"] == record

    def test_auction_result_is_a_valid_player_message_type(self):
        payload = build_player_payload(game_id="g1", username="alice", message_type="AUCTION_RESULT", prompt="x")
        assert payload["message_type"] == "AUCTION_RESULT"

    def test_spectator_payload_carries_data_verbatim_when_given(self):
        record = {"round_number": 1, "recipient": "alice", "price_paid": 8}
        payload = build_spectator_payload(game_id="g1", message_type="AUCTION_RESULT", prompt="result", data=record)
        assert payload["data"] == record

    def test_spectator_payload_omits_data_key_when_not_given(self):
        payload = build_spectator_payload(game_id="g1", message_type="GLOBAL_EVENT", prompt="hi")
        assert "data" not in payload
