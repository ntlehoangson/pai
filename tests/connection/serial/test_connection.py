import logging
from unittest import mock

import pytest
from serial import SerialException

from paradox.connections.serial.connection import SerialCommunication


@pytest.mark.asyncio
async def test_serial_open_failure_returns_false_for_retry(monkeypatch):
    connection = SerialCommunication("/dev/serial/by-id/usb-missing", 9600)

    async def serial_open_failure(*args, **kwargs):
        raise SerialException("adapter not present")

    monkeypatch.setattr(
        "paradox.connections.serial.connection.os.access", lambda *args, **kwargs: True
    )
    with mock.patch(
        "paradox.connections.serial.connection.serial_asyncio.create_serial_connection",
        side_effect=serial_open_failure,
    ):
        assert await connection.connect() is False

    assert connection.connected is False


def test_local_serial_close_is_not_logged_as_connection_loss(caplog):
    connection = SerialCommunication("/dev/ttyS1", 9600)
    connection.connected_future = mock.MagicMock()
    connection.connected_future.done.return_value = True
    connection._closing = True

    with caplog.at_level(logging.INFO):
        connection.on_connection_loss()

    assert "Serial connection closed by local shutdown" in caplog.text
    assert "Connection to panel was lost" not in caplog.text


def test_unexpected_serial_close_remains_an_error(caplog):
    connection = SerialCommunication("/dev/ttyS1", 9600)
    connection.connected_future = mock.MagicMock()
    connection.connected_future.done.return_value = True

    with caplog.at_level(logging.ERROR):
        connection.on_connection_loss()

    assert "Connection to panel was lost" in caplog.text
