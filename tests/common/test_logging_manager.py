from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager


def test_errors_reach_stderr_but_routine_info_does_not(capsys):
    """Render only shows a service's stdout/stderr -- the log files on the
    instance's own disk are never seen -- so a warning or error must reach
    stderr too, while routine INFO lines stay in the files only."""
    LoggingManager.set_level("INFO")
    LoggingManager.info("routine bid narration")
    LoggingManager.error("game thread crashed")
    err = capsys.readouterr().err
    assert "game thread crashed" in err
    assert "routine bid narration" not in err


def test_a_thread_logging_during_first_time_setup_waits_instead_of_crashing(monkeypatch):
    """Right after a restart, several game threads logged at once; the
    singleton was published before its loggers existed, so the others got
    AttributeError: no attribute '_generic_logger' (9 of 10 live games died
    at startup). Logging during setup must wait for it to finish."""
    import logging
    import threading

    monkeypatch.setattr(LoggingManager, "_instance", None)
    monkeypatch.delattr(LoggingManager, "_generic_logger", raising=False)
    monkeypatch.delattr(LoggingManager, "_security_logger", raising=False)
    setup_started = threading.Event()

    def slow_initialize(cls, *args, **kwargs):
        setup_started.set()
        threading.Event().wait(0.3)  # time.sleep is patched to a no-op in this suite
        cls._generic_logger = logging.getLogger("test-generic")
        cls._security_logger = logging.getLogger("test-security")

    monkeypatch.setattr(LoggingManager, "_initialize", classmethod(slow_initialize))
    monkeypatch.setattr("highsociety.code.common.utils.utility.get_all_configurations", lambda: {})
    errors = []

    def log_from_a_game_thread():
        try:
            LoggingManager.info("game starting")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    first = threading.Thread(target=log_from_a_game_thread)
    first.start()
    setup_started.wait(2)
    others = [threading.Thread(target=log_from_a_game_thread) for _ in range(5)]
    for t in others:
        t.start()
    for t in [first, *others]:
        t.join(5)
    assert errors == []
