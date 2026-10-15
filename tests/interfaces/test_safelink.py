import asyncio
import json
import stat
from types import SimpleNamespace

import pytest
import requests

from paradox.interfaces import safelink as safelink_module
from paradox.interfaces.safelink import (
    Delivery,
    SafeLinkInterface,
    make_webhook_payload,
    validate_serial_config,
)


def serial_config(**overrides):
    value = {
        "CONNECTION_TYPE": "Serial",
        "SERIAL_PORT": "/dev/serial/by-id/usb-test",
        "SERIAL_BAUD": 9600,
        "SERIAL_ENCRYPTED": False,
        "PASSWORD": "0000",
    }
    value.update(overrides)
    return value


def interface_config(tmp_path):
    return SimpleNamespace(
        SAFELINK_CRAWL_URL="https://example.invalid/crawl",
        **serial_config(),
    )


@pytest.fixture(autouse=True)
def isolate_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(safelink_module, "SERIAL_CONFIG_FILE", tmp_path / "serial.json")
    monkeypatch.setattr(safelink_module, "API_BIND_ADDRESS", "127.0.0.1")
    monkeypatch.setattr(safelink_module, "API_BIND_PORT", 0)
    monkeypatch.setattr(safelink_module, "MAX_RETRIES", 0)


class FakeAlarm:
    def __init__(self):
        self.storage = SimpleNamespace(data={})
        self.reconfigured = None

    async def reconfigure_connection(self, value):
        self.reconfigured = value


class FakeResponse:
    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


def test_validate_serial_config_preserves_exact_contract():
    value = serial_config()

    assert validate_serial_config(value) == value


@pytest.mark.parametrize(
    "override,error",
    [
        ({"CONNECTION_TYPE": "IP"}, "CONNECTION_TYPE"),
        ({"SERIAL_PORT": ""}, "SERIAL_PORT"),
        ({"SERIAL_BAUD": True}, "SERIAL_BAUD"),
        ({"SERIAL_ENCRYPTED": "false"}, "SERIAL_ENCRYPTED"),
        ({"PASSWORD": 0}, "PASSWORD"),
        ({"PASSWORD": "123"}, "PASSWORD"),
    ],
)
def test_validate_serial_config_rejects_bad_values(override, error):
    with pytest.raises(ValueError, match=error):
        validate_serial_config(serial_config(**override))


def test_webhook_wrapper_does_not_modify_data():
    data = {"1": {"id": 1, "open": True, "tamper": False}}

    payload = make_webhook_payload("heatbeath", data)

    assert payload == {"type": "heatbeath", "data": data}
    assert payload["data"] is not data


@pytest.mark.asyncio
async def test_outbound_json_has_only_type_and_unchanged_data(tmp_path):
    service = SafeLinkInterface(FakeAlarm(), interface_config(tmp_path))
    session = FakeSession()
    data = {1: {"id": 1, "open": True, "tamper": False}}

    service._deliver(
        session,
        Delivery(make_webhook_payload("heatbeath", data), "heartbeat"),
    )

    assert len(session.calls) == 1
    url, request = session.calls[0]
    assert url == "https://example.invalid/crawl"
    assert json.loads(request["data"]) == {
        "type": "heatbeath",
        "data": {"1": data[1]},
    }


@pytest.mark.asyncio
async def test_live_event_uses_exact_type_and_event_props(tmp_path):
    alarm = FakeAlarm()
    service = SafeLinkInterface(alarm, interface_config(tmp_path))
    props = {"type": "zone", "id": 3, "change": {"open": True}}
    event = SimpleNamespace(props=props)

    service._on_event(event)

    delivery = service._outbound.get_nowait()
    assert delivery == Delivery({"type": "typeevent", "data": props}, "live event")


@pytest.mark.asyncio
async def test_status_snapshot_keeps_raw_zone_map(tmp_path):
    alarm = FakeAlarm()
    alarm.storage.data = {
        "zone": {1: {"id": 1, "label": "Front", "open": False}},
        "partition": {1: {"id": 1, "arm": False}},
    }
    service = SafeLinkInterface(alarm, interface_config(tmp_path))

    service._on_status_update(status={"zone": {1: {"open": False}}})

    assert service.get_zones() == {1: {"id": 1, "label": "Front", "open": False}}
    assert service.get_panel_details()["data"] == alarm.storage.data


@pytest.mark.asyncio
async def test_serial_update_is_persisted_and_reconnects(tmp_path):
    alarm = FakeAlarm()
    conf = interface_config(tmp_path)
    service = SafeLinkInterface(alarm, conf)
    updated = serial_config(PASSWORD="12AF")

    response = service.update_serial_config(updated)
    await asyncio.sleep(0.01)

    assert response["accepted"] is True
    assert response["config"]["PASSWORD"] == "12AF"
    assert alarm.reconfigured == updated
    runtime_file = tmp_path / "serial.json"
    assert json.loads(runtime_file.read_text("utf-8")) == updated
    assert stat.S_IMODE(runtime_file.stat().st_mode) == 0o666


@pytest.mark.asyncio
async def test_http_api_is_open_and_returns_password(tmp_path):
    alarm = FakeAlarm()
    alarm.storage.data = {"zone": {1: {"id": 1, "open": False}}}
    conf = interface_config(tmp_path)
    service = SafeLinkInterface(alarm, conf)
    service._on_status_update(status={"zone": {1: {"open": False}}})
    service.start()
    port = service._server.server_address[1]
    url = f"http://127.0.0.1:{port}/api/v1/config/serial"

    try:
        serial_response = requests.get(url, timeout=2)
        zones = requests.get(
            f"http://127.0.0.1:{port}/api/v1/zones",
            timeout=2,
        )
        panel = requests.get(
            f"http://127.0.0.1:{port}/api/v1/panel",
            timeout=2,
        )
    finally:
        service.stop()

    assert serial_response.status_code == 200
    assert serial_response.json()["PASSWORD"] == "0000"
    assert zones.json() == {"1": {"id": 1, "open": False}}
    assert panel.json()["data"] == {"zone": zones.json()}
