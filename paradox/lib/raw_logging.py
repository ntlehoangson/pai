"""Raw transport logging for panel traffic."""

import binascii
import logging

from paradox.config import config as cfg

tx_logger = logging.getLogger("PAI.raw.tx")
rx_logger = logging.getLogger("PAI.raw.rx")


def _enabled() -> bool:
    return bool(
        cfg.LOGGING_DUMP_PACKETS
        or cfg.LOGGING_RAW_TX_FILE
        or cfg.LOGGING_RAW_RX_FILE
    )


def log_tx(data: bytes) -> None:
    if _enabled() and data:
        try:
            tx_logger.info("RAW %s", binascii.hexlify(data).decode("ascii"))
        except Exception:
            pass


def log_rx(data: bytes) -> None:
    if _enabled() and data:
        try:
            rx_logger.info("RAW %s", binascii.hexlify(data).decode("ascii"))
        except Exception:
            pass


def log_decoded(message) -> None:
    """Write decoded panel data without ever affecting panel processing."""
    if _enabled():
        try:
            rx_logger.info("DECODED %r", message)
        except Exception:
            pass
