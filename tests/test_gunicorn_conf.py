import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("gunicorn")


def _load_conf():
    path = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
    spec = importlib.util.spec_from_file_location("gunicorn_conf", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def logged(monkeypatch):
    conf = _load_conf()
    lines = []
    monkeypatch.setattr(conf.Logger, "access", lambda self, resp, req, environ, request_time: lines.append(environ["PATH_INFO"]))
    logger = conf.QuietPollLogger.__new__(conf.QuietPollLogger)

    def request(path, status):
        logger.access(SimpleNamespace(status_code=status), None, {"PATH_INFO": path}, 0.01)

    return lines, request


def test_successful_polls_are_not_logged(logged):
    lines, request = logged
    for path in ("/api/rooms", "/api/status", "/api/matchmaking/status", "/api/matchmaking/queue"):
        request(path, 200)
    assert lines == []


def test_failed_polls_and_other_requests_are_still_logged(logged):
    lines, request = logged
    request("/api/rooms", 500)
    request("/api/status", 404)
    request("/api/leaderboard", 200)
    request("/", 200)
    assert lines == ["/api/rooms", "/api/status", "/api/leaderboard", "/"]


def test_gunicorn_uses_the_quiet_logger():
    conf = _load_conf()
    assert conf.logger_class is conf.QuietPollLogger
