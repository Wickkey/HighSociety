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
