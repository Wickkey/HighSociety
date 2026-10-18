from unittest.mock import MagicMock, patch

from highsociety.code.common import notify


def test_unconfigured_is_a_silent_no_op(monkeypatch):
    monkeypatch.setenv("NOTIFY_SMTP_USER", "")
    monkeypatch.setenv("NOTIFY_SMTP_PASSWORD", "")
    with patch("smtplib.SMTP_SSL") as smtp:
        assert notify.send_email("s", "b") is False
    smtp.assert_not_called()


def test_sends_through_gmail_with_the_app_password_and_defaults_the_recipient(monkeypatch):
    monkeypatch.setenv("NOTIFY_SMTP_USER", "me@gmail.com")
    monkeypatch.setenv("NOTIFY_SMTP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "")
    server = MagicMock()
    with patch("smtplib.SMTP_SSL") as smtp:
        smtp.return_value.__enter__.return_value = server
        assert notify.send_email("Self-play: 1,000 / 10,000 games", "body") is True
    assert smtp.call_args.args == ("smtp.gmail.com", 465)
    server.login.assert_called_once_with("me@gmail.com", "abcdefghijklmnop")  # spaces Google shows are dropped
    message = server.send_message.call_args.args[0]
    assert message["To"] == "me@gmail.com" and message["Subject"] == "Self-play: 1,000 / 10,000 games"


def test_a_failed_send_never_raises(monkeypatch):
    monkeypatch.setenv("NOTIFY_SMTP_USER", "me@gmail.com")
    monkeypatch.setenv("NOTIFY_SMTP_PASSWORD", "x")
    with patch("smtplib.SMTP_SSL", side_effect=OSError("network down")):
        assert notify.send_email("s", "b") is False
