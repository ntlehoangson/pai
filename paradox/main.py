import asyncio
import gzip
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
import os
import platform
import queue
import shutil
import signal
import sys
import threading
import time

from paradox import VERSION
from paradox.config import config as cfg
from paradox.exceptions import PAICriticalException
from paradox.interfaces.interface_manager import InterfaceManager
from paradox.lib.encodings import register_encodings
from paradox.lib.utils import describe_connection, format_duration
from paradox.paradox import Paradox

logger = logging.getLogger("PAI")
RAW_TX_LOGGER = logging.getLogger("PAI.raw.tx")
RAW_RX_LOGGER = logging.getLogger("PAI.raw.rx")


class _AsyncLogHandler(logging.Handler):
    """Queue file writes so raw diagnostics never run on the panel loop."""

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.records = queue.SimpleQueue()
        self.worker = threading.Thread(target=self._write, daemon=True)
        self.worker.start()

    def emit(self, record):
        self.records.put(record)

    def _write(self):
        while True:
            record = self.records.get()
            try:
                self.target.handle(record)
            except Exception:
                pass


def _gzip_rotator(source, destination):
    """Compress a completed log rotation without retaining the plain file."""
    with open(source, "rb") as source_stream:
        with gzip.open(destination, "wb") as destination_stream:
            shutil.copyfileobj(source_stream, destination_stream)
    os.remove(source)


def _build_logfile_handler(filename=None, rotation=None, compress=None, max_files=None):
    filename = cfg.LOGGING_FILE if filename is None else filename
    rotation = cfg.LOGGING_FILE_ROTATION if rotation is None else rotation
    compress = cfg.LOGGING_FILE_COMPRESS if compress is None else compress
    max_files = cfg.LOGGING_FILE_MAX_FILES if max_files is None else max_files
    parent = os.path.dirname(os.path.abspath(filename))
    if parent:
        os.makedirs(parent, exist_ok=True)

    if rotation == "daily":
        handler = TimedRotatingFileHandler(
            filename,
            when="midnight",
            interval=1,
            backupCount=max_files,
            encoding="utf-8",
            delay=False,
        )
        handler.suffix = "%Y-%m-%d"
    else:
        handler = RotatingFileHandler(
            filename,
            mode="a",
            maxBytes=cfg.LOGGING_FILE_MAX_SIZE * 1024 * 1024,
            backupCount=max_files,
            encoding="utf-8",
            delay=False,
        )

    if compress:
        handler.namer = lambda filename: filename + ".gz"
        handler.rotator = _gzip_rotator
    return handler


def get_format(level):
    if level <= logging.DEBUG:
        return (
            "%(asctime)s - %(levelname)-8s - %(threadName)-10s - %(name)s - %(message)s"
        )
    else:
        return "%(asctime)s - %(levelname)-8s - %(name)s - %(message)s"


def configure_logger(logger):
    logger_level = cfg.LOGGING_LEVEL_CONSOLE

    if cfg.LOGGING_FILE:
        logfile_handler = _build_logfile_handler()

        logfile_handler.setLevel(cfg.LOGGING_LEVEL_FILE)
        logfile_handler.setFormatter(logging.Formatter(get_format(logger_level)))
        logger.addHandler(logfile_handler)
        logger_level = min(logger_level, cfg.LOGGING_LEVEL_FILE)

    logconsole_handler = logging.StreamHandler()
    logconsole_handler.setLevel(cfg.LOGGING_LEVEL_CONSOLE)
    logconsole_handler.setFormatter(logging.Formatter(get_format(logger_level)))
    logger.addHandler(logconsole_handler)

    logger.setLevel(logger_level)

    for raw_logger, filename in (
        (RAW_TX_LOGGER, cfg.LOGGING_RAW_TX_FILE),
        (RAW_RX_LOGGER, cfg.LOGGING_RAW_RX_FILE),
    ):
        if not filename:
            continue
        handler = _build_logfile_handler(
            filename=filename,
            rotation="daily",
            compress=cfg.LOGGING_RAW_COMPRESS,
            max_files=cfg.LOGGING_RAW_MAX_FILES,
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        raw_logger.addHandler(_AsyncLogHandler(handler))
        raw_logger.setLevel(logging.INFO)
        raw_logger.propagate = False


async def _run(alarm: Paradox):
    interface_manager = InterfaceManager(alarm, config=cfg)
    interface_manager.start()
    shutting_down = False

    logger.info("=" * 56)
    logger.info(" PAI %s", VERSION)
    logger.info(" Python %s on %s", platform.python_version(), platform.platform())
    logger.info(" Connection: %s", describe_connection())
    logger.info(
        " Interfaces: %s",
        ", ".join(
            getattr(i, "name", type(i).__name__) for i in interface_manager.interfaces
        )
        or "none",
    )
    logger.info("=" * 56)

    async def exit_handler(signame=None):
        nonlocal alarm, interface_manager, shutting_down

        shutting_down = True

        if signame is not None:
            logger.info(f"Captured signal {signame}. Exiting")

        if alarm:
            await alarm.disconnect()
            alarm = None

        if interface_manager:
            interface_manager.stop()
            interface_manager = None

        logger.info("Good bye!")

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame)
        try:
            loop.add_signal_handler(
                sig, lambda s=signame: asyncio.ensure_future(exit_handler(s))
            )
        except NotImplementedError:
            logger.debug("Async signal handlers are unavailable on this platform")

    retry = 1
    connected_since = None
    disconnected_at = None

    def mark_connected():
        nonlocal connected_since, disconnected_at
        if disconnected_at is not None:
            logger.warning(
                "Connection recovered after %s down",
                format_duration(time.monotonic() - disconnected_at),
            )
        connected_since = time.monotonic()
        disconnected_at = None

    def mark_disconnected():
        nonlocal connected_since, disconnected_at
        if connected_since is None:
            # Never reached a healthy session, so there is no uptime to report
            # and nothing to "recover" from on the next successful attempt.
            return
        logger.warning(
            "Panel connection ended after %s up",
            format_duration(time.monotonic() - connected_since),
        )
        connected_since = None
        disconnected_at = time.monotonic()

    while alarm is not None:
        logger.info("Starting...")
        retry_time_wait = 2 ^ retry
        retry_time_wait = 30 if retry_time_wait > 30 else retry_time_wait

        try:
            if await alarm.full_connect():
                retry = 1
                mark_connected()
                await alarm.loop()
            else:
                logger.error("Unable to connect to alarm via %s", describe_connection())
            if not shutting_down:
                mark_disconnected()

            if alarm:
                await asyncio.sleep(retry_time_wait)
        except ConnectionError as e:  # Connection to IP Module or MQTT lost
            mark_disconnected()
            logger.error(
                "Connection to panel lost via %s: %s. Restarting",
                describe_connection(),
                e,
            )
            await asyncio.sleep(retry_time_wait)
        except OSError:  # Connection to IP Module or MQTT lost
            mark_disconnected()
            logger.exception("Restarting")
            await asyncio.sleep(retry_time_wait)
        except PAICriticalException:
            mark_disconnected()
            logger.exception("PAI Critical exception. Stopping PAI")
            break
        except (KeyboardInterrupt, SystemExit):
            mark_disconnected()
            break  # break exits the retry loop
        except Exception:
            mark_disconnected()
            logger.exception("Restarting")
            await asyncio.sleep(retry_time_wait)

        retry += 1

    if alarm:
        await exit_handler()


def main(args):
    time.tzset()
    if "config" in args and args.config is not None:
        import os

        config_file = os.path.abspath(args.config)
        cfg.load(config_file)
    else:
        cfg.load()

    configure_logger(logger)

    logger.info(f"Starting Paradox Alarm Interface {VERSION}")
    logger.info(f"Config loaded from {cfg.CONFIG_FILE_LOCATION}")

    logger.info(f"Console Log level set to {cfg.LOGGING_LEVEL_CONSOLE}")

    # Registering additional encodings
    register_encodings()

    # Start interacting with the alarm
    asyncio.run(_run(Paradox()))

    sys.exit(0)
