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
