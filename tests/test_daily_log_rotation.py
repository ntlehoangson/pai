import gzip
import logging

from paradox import main


def test_daily_log_rotation_compresses_completed_file(monkeypatch, tmp_path):
    log_file = tmp_path / "paradosp.log"
    monkeypatch.setattr(main.cfg, "LOGGING_FILE", str(log_file))
    monkeypatch.setattr(main.cfg, "LOGGING_FILE_ROTATION", "daily")
    monkeypatch.setattr(main.cfg, "LOGGING_FILE_COMPRESS", True)
    monkeypatch.setattr(main.cfg, "LOGGING_FILE_MAX_FILES", 30)

    handler = main._build_logfile_handler()
    try:
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(
            logging.LogRecord(
                "PAI.test",
                logging.INFO,
                __file__,
                1,
                "before rollover",
                (),
                None,
            )
        )
        handler.doRollover()
    finally:
        handler.close()

    archives = list(tmp_path.glob("paradosp.log.*.gz"))
    assert len(archives) == 1
    with gzip.open(archives[0], "rt", encoding="utf-8") as stream:
        assert stream.read().strip() == "before rollover"
