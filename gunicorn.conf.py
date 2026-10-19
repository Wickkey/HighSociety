"""
gunicorn loads this file from the working directory on startup (the
Procfile / Render start command runs from the repo root).

Render runs gunicorn with --access-logfile -, so every request gets an
access line in the service log. The browser polls a few endpoints every
1-3 seconds while a screen is open (the public rooms list, a room's
status, the matchmaking queue), and those lines drowned everything else:
711 of the last 1,000 production log lines were "GET /api/rooms 200".
Successful polls are now left out; a poll that fails, and every other
request, is still logged.
"""
from gunicorn.glogging import Logger

QUIET_POLL_PATHS = frozenset({
    "/api/rooms",
    "/api/status",
    "/api/matchmaking/status",
    "/api/matchmaking/queue",
})


class QuietPollLogger(Logger):
    def access(self, resp, req, environ, request_time):
        if environ.get("PATH_INFO") in QUIET_POLL_PATHS and (resp.status_code or 0) < 400:
            return
        super().access(resp, req, environ, request_time)


logger_class = QuietPollLogger
