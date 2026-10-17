"""SafeLink serial configuration API and non-blocking webhook delivery.

The serial/event asyncio loop must never wait for an HTTP request.  Pub-sub
callbacks only take an immutable snapshot and enqueue work; a dedicated worker
performs all outbound network I/O.
"""

import asyncio
from collections import namedtuple
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import requests

from paradox.interfaces import Interface
from paradox.lib import ps
from paradox.lib.utils import JSONByteEncoder, construct_free

logger = logging.getLogger("PAI").getChild(__name__)

SERIAL_CONFIG_FIELDS = (
    "CONNECTION_TYPE",
    "SERIAL_PORT",
    "SERIAL_BAUD",
    "SERIAL_ENCRYPTED",
    "PASSWORD",
)
PASSWORD_RE = re.compile(r"^[0-9A-Fa-f]{4}$")
MAX_API_BODY_BYTES = 64 * 1024

# Fixed deployment contract for the custom Orange Pi build. Keeping these out
# of paradox.config leaves operators with one SafeLink setting: the crawl URL.
API_BIND_ADDRESS = "0.0.0.0"
API_BIND_PORT = 8082
SERIAL_CONFIG_FILE = Path.home() / ".pai" / "safelink-serial.json"
SERIAL_CONFIG_FILE_MODE = 0o666
PUSH_INTERVAL_SECONDS = 60
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3
EVENT_QUEUE_SIZE = 256
HEARTBEAT_TYPE = "heartbeat"
IMMEDIATELY_TYPE = "immediately"
NOW_METADATA_TYPE = "nowMetadata"
NOISY_POWER_PROPERTIES = frozenset(("battery", "dc", "vdc"))

Delivery = namedtuple("Delivery", "payload description")


def validate_serial_config(value: Any) -> Dict[str, Any]:
    """Validate and return an isolated runtime serial configuration."""
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object")

    missing = [key for key in SERIAL_CONFIG_FIELDS if key not in value]
    unknown = [key for key in value if key not in SERIAL_CONFIG_FIELDS]
    if missing:
        raise ValueError("Missing fields: {}".format(", ".join(missing)))
    if unknown:
        raise ValueError("Unknown fields: {}".format(", ".join(unknown)))

    if value["CONNECTION_TYPE"] != "Serial":
        raise ValueError('CONNECTION_TYPE must be exactly "Serial"')

    serial_port = value["SERIAL_PORT"]
    if not isinstance(serial_port, str) or not serial_port.strip():
        raise ValueError("SERIAL_PORT must be a non-empty string")
    if "\x00" in serial_port or len(serial_port) > 1024:
        raise ValueError("SERIAL_PORT is invalid")

    serial_baud = value["SERIAL_BAUD"]
    if (
        isinstance(serial_baud, bool)
        or not isinstance(serial_baud, int)
        or serial_baud < 300
        or serial_baud > 1_000_000
    ):
        raise ValueError("SERIAL_BAUD must be an integer between 300 and 1000000")

    serial_encrypted = value["SERIAL_ENCRYPTED"]
    if not isinstance(serial_encrypted, bool):
        raise ValueError("SERIAL_ENCRYPTED must be a boolean")

    password = value["PASSWORD"]
    if not isinstance(password, str) or not PASSWORD_RE.fullmatch(password):
        raise ValueError("PASSWORD must contain exactly 4 hexadecimal characters")

    return {
        "CONNECTION_TYPE": "Serial",
        "SERIAL_PORT": serial_port.strip(),
        "SERIAL_BAUD": serial_baud,
        "SERIAL_ENCRYPTED": serial_encrypted,
        "PASSWORD": password,
    }


def make_webhook_payload(type_value: str, data: Any) -> Dict[str, Any]:
    """Wrap data without renaming, flattening or enriching any data field."""
    return {"type": type_value, "data": deepcopy(data)}


def is_debug_power_telemetry(props: Any) -> bool:
    """Return True only for routine voltage samples, never power alarms."""
    if not isinstance(props, Mapping):
        return False

    level = str(props.get("level", "")).upper()
    property_name = str(props.get("property", "")).lower()
    tags = props.get("tags", ())
    if not isinstance(tags, (list, tuple, set, frozenset)):
        tags = ()

    return (
        level == "DEBUG"
        and property_name in NOISY_POWER_PROPERTIES
        and "voltage" in {str(tag).lower() for tag in tags}
    )


class _SafeLinkHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], service: "SafeLinkInterface"):
        self.service = service
        super().__init__(address, _SafeLinkRequestHandler)


class _SafeLinkRequestHandler(BaseHTTPRequestHandler):
    server_version = "PAI-SafeLink/1"

    @property
    def service(self) -> "SafeLinkInterface":
        return self.server.service

    def log_message(self, fmt, *args):
        logger.debug("SafeLink API %s - %s", self.client_address[0], fmt % args)

    def _write_json(self, status: int, value: Any):
        body = json.dumps(
            value, cls=JSONByteEncoder, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _path(self) -> str:
        return urlsplit(self.path).path.rstrip("/") or "/"

    def do_GET(self):
        path = self._path()
        if path == "/api/v1/health":
            self._write_json(HTTPStatus.OK, self.service.get_health())
        elif path == "/api/v1/config/serial":
            self._write_json(HTTPStatus.OK, self.service.get_public_serial_config())
        elif path == "/api/v1/zones":
            self._write_json(HTTPStatus.OK, self.service.get_zones())
        elif path == "/api/v1/panel":
            self._write_json(HTTPStatus.OK, self.service.get_panel_details())
        else:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self):
        self._handle_serial_update()

    def do_PUT(self):
        self._handle_serial_update()

    def _handle_serial_update(self):
        if self._path() != "/api/v1/config/serial":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if content_length <= 0 or content_length > MAX_API_BODY_BYTES:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "invalid_content_length"},
            )
            return

        try:
            body = self.rfile.read(content_length)
            value = json.loads(body.decode("utf-8"))
            result = self.service.update_serial_config(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_json", "message": str(exc)},
            )
            return
        except ValueError as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_config", "message": str(exc)},
            )
            return
        except OSError:
            logger.exception("Unable to persist SafeLink serial configuration")
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "config_persist_failed"},
            )
            return

        self._write_json(HTTPStatus.ACCEPTED, result)


class SafeLinkInterface(Interface):
    """Expose panel state and push heartbeat/live events to SafeLink."""

    def __init__(self, alarm, config):
        super().__init__(alarm)
        self.conf = config
        self._main_loop = asyncio.get_running_loop()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._outbound = queue.Queue(maxsize=EVENT_QUEUE_SIZE)
        self._worker_thread: Optional[threading.Thread] = None
        self._api_thread: Optional[threading.Thread] = None
        self._server: Optional[_SafeLinkHTTPServer] = None
        self._panel = None
        self._run_state = "STOP"
        self._zones = None
        self._storage = {}
        self._last_status_at = None
        self._last_push_at = None
        self._last_heartbeat_at = None
        self._last_event_push_at = None
        self._last_push_error = None
        self._dropped_events = 0
        self._filtered_events = 0
        self._heartbeat_push_count = 0
        self._event_push_count = 0
        self._heartbeat_requested = threading.Event()
        self._now_metadata_pending = False

        # This runs before Paradox.full_connect(), so persisted settings are
        # guaranteed to be applied before the serial transport is constructed.
        self._load_serial_config()

    def start(self):
        self._server = _SafeLinkHTTPServer((API_BIND_ADDRESS, API_BIND_PORT), self)
        self._subscribe()

        self._api_thread = threading.Thread(
            target=self._serve_api,
            name="SafeLinkAPI",
            daemon=True,
        )
        self._worker_thread = threading.Thread(
            target=self._delivery_loop,
            name="SafeLinkPush",
            daemon=True,
        )
        self._api_thread.start()
        self._worker_thread.start()
        logger.info(
            "SafeLink API listening on %s:%s",
            API_BIND_ADDRESS,
            API_BIND_PORT,
        )

    def stop(self):
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._unsubscribe()
        for thread in (self._api_thread, self._worker_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=10)

    def _subscribe(self):
        ps.subscribe(self._on_panel_detected, "panel_detected")
        ps.subscribe(self._on_status_update, "status_update")
        ps.subscribe(self._on_event, "events")
        ps.subscribe(self._on_run_state, "run-state")

    def _unsubscribe(self):
        for callback, topic in (
            (self._on_panel_detected, "panel_detected"),
            (self._on_status_update, "status_update"),
            (self._on_event, "events"),
            (self._on_run_state, "run-state"),
        ):
            try:
                ps.pub.unsubscribe(callback, ps.PREFIX + topic)
            except ValueError:
                pass

    def _serve_api(self):
        try:
            self._server.serve_forever(poll_interval=0.5)
        except Exception:
            if not self._stop.is_set():
                logger.exception("SafeLink API server failed")

    def _delivery_loop(self):
        session = requests.Session()
        next_heartbeat = time.monotonic() + PUSH_INTERVAL_SECONDS
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if self._heartbeat_requested.is_set() or now >= next_heartbeat:
                    self._heartbeat_requested.clear()
                    zones = self.get_zones(allow_unavailable=True)
                    if zones is not None:
                        payload = make_webhook_payload(HEARTBEAT_TYPE, zones)
                        self._deliver(session, Delivery(payload, "heartbeat"))
                    else:
                        logger.debug(
                            "SafeLink heartbeat waiting for the first zone snapshot"
                        )
                    next_heartbeat = time.monotonic() + PUSH_INTERVAL_SECONDS
                    continue

                wait = max(0.0, next_heartbeat - time.monotonic())
                try:
                    delivery = self._outbound.get(timeout=min(wait, 1.0))
                except queue.Empty:
                    delivery = None

                if delivery is not None:
                    self._deliver(session, delivery)
                    self._outbound.task_done()
        finally:
            session.close()

    def _deliver(self, session: requests.Session, delivery: Delivery):
        headers = {"Content-Type": "application/json"}
        try:
            body = json.dumps(
                delivery.payload,
                cls=JSONByteEncoder,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (UnicodeError, TypeError, ValueError) as exc:
            with self._lock:
                self._last_push_error = str(exc)
            logger.error(
                "SafeLink %s cannot be encoded as JSON: %s",
                delivery.description,
                exc,
            )
            return False

        attempts = MAX_RETRIES + 1
        for attempt in range(attempts):
            if self._stop.is_set():
                return False
            try:
                response = session.post(
                    self.conf.SAFELINK_CRAWL_URL,
                    data=body,
                    headers=headers,
                    timeout=(
                        CONNECT_TIMEOUT_SECONDS,
                        READ_TIMEOUT_SECONDS,
                    ),
                )
                response.raise_for_status()
                payload_type = delivery.payload.get("type")
                pushed_at = time.time()
                with self._lock:
                    self._last_push_at = pushed_at
                    self._last_push_error = None
                    if payload_type == HEARTBEAT_TYPE:
                        self._last_heartbeat_at = pushed_at
                        self._heartbeat_push_count += 1
                    elif payload_type == IMMEDIATELY_TYPE:
                        self._last_event_push_at = pushed_at
                        self._event_push_count += 1
                if payload_type == HEARTBEAT_TYPE:
                    zone_count = len(delivery.payload.get("data", {}))
                    logger.info("SafeLink heartbeat delivered: %d zone(s)", zone_count)
                else:
                    logger.debug("SafeLink %s delivered", delivery.description)
                return True
            except (
                requests.RequestException,
                UnicodeError,
                TypeError,
                ValueError,
            ) as exc:
                error_detail = self._format_delivery_error(exc)
                with self._lock:
                    self._last_push_error = error_detail
                if attempt + 1 >= attempts:
                    logger.error(
                        "SafeLink %s delivery failed after %d attempt(s) "
                        "(payload=%d bytes): %s",
                        delivery.description,
                        attempts,
                        len(body),
                        error_detail,
                    )
                    return False
                self._stop.wait(min(2**attempt, 30))
        return False

    @staticmethod
    def _format_delivery_error(exc):
        """Include a bounded server response body in HTTP diagnostics."""
        detail = str(exc)
        response = getattr(exc, "response", None)
        if response is None:
            return detail

        response_text = (getattr(response, "text", "") or "").strip()
        if not response_text:
            return detail

        response_text = " ".join(response_text.split())
        return "{}; response={}".format(detail, response_text[:1024])

    def _on_panel_detected(self, panel):
        with self._lock:
            self._panel = dict(panel._asdict())

    def _on_run_state(self, state):
        state_name = getattr(state, "name", str(state))
        with self._lock:
            self._run_state = state_name
            if state_name in {"STOP", "ERROR", "INIT"}:
                self._panel = None
                self._zones = None
                self._storage = {}
                self._last_status_at = None

    def _on_status_update(self, status):
        # Paradox's storage callback is registered before this interface and is
        # synchronous, so storage already contains this status at this point.
        storage = construct_free(deepcopy(dict(self.alarm.storage.data)))
        zones = deepcopy(storage.get("zone", {}))
        with self._lock:
            first_zone_snapshot = self._zones is None
            now_metadata_requested = self._now_metadata_pending
            self._now_metadata_pending = False
            self._storage = storage
            self._zones = zones
            self._last_status_at = time.time()
        if first_zone_snapshot:
            self._heartbeat_requested.set()
        if now_metadata_requested:
            self._enqueue_delivery(
                make_webhook_payload(NOW_METADATA_TYPE, zones),
                "current status after live event",
            )

    def _on_event(self, event):
        props = event.props
        if is_debug_power_telemetry(props):
            with self._lock:
                self._filtered_events += 1
            logger.debug(
                "SafeLink filtered routine power telemetry: %s=%s",
                props.get("property"),
                props.get("value"),
            )
            return

        payload = make_webhook_payload(IMMEDIATELY_TYPE, props)
        self._enqueue_delivery(payload, "live event")

        # Live events are transitions, not a complete panel snapshot. Ask
        # SP4000 for its current status and publish that snapshot separately.
        if self._is_sp4000_event(props):
            with self._lock:
                self._now_metadata_pending = True
            request_status_refresh = getattr(self.alarm, "request_status_refresh", None)
            if callable(request_status_refresh):
                request_status_refresh()

    def _enqueue_delivery(self, payload, description):
        try:
            self._outbound.put_nowait(Delivery(payload, description))
        except queue.Full:
            with self._lock:
                self._dropped_events += 1
            logger.error("SafeLink delivery queue full; %s was not delivered", description)

    def _is_sp4000_event(self, props):
        """Return whether an event can be confirmed with a panel status read."""
        panel = getattr(self.alarm, "panel", None)
        if panel is None:
            # Keep the callback independently testable before panel discovery.
            return props.get("type") == "zone"
        module = type(panel).__module__
        return "spectra_magellan" in module

    def get_zones(self, allow_unavailable: bool = False):
        with self._lock:
            if self._zones is None:
                if allow_unavailable:
                    return None
                return {
                    "available": False,
                    "reason": "No full panel status has been received yet",
                }
            return deepcopy(self._zones)

    def get_panel_details(self):
        with self._lock:
            return {
                "panel": deepcopy(self._panel),
                "run_state": self._run_state,
                "last_status_at": self._last_status_at,
                "connection": self.get_public_serial_config(),
                "data": deepcopy(self._storage),
            }

    def get_health(self):
        with self._lock:
            return {
                "status": "ok",
                "run_state": self._run_state,
                "panel_detected": self._panel is not None,
                "zones_available": self._zones is not None,
                "last_status_at": self._last_status_at,
                "last_push_at": self._last_push_at,
                "last_heartbeat_at": self._last_heartbeat_at,
                "last_event_push_at": self._last_event_push_at,
                "last_push_error": self._last_push_error,
                "queued_events": self._outbound.qsize(),
                "dropped_events": self._dropped_events,
                "filtered_events": self._filtered_events,
                "heartbeat_push_count": self._heartbeat_push_count,
                "event_push_count": self._event_push_count,
            }

    def get_public_serial_config(self):
        return {
            "CONNECTION_TYPE": self.conf.CONNECTION_TYPE,
            "SERIAL_PORT": self.conf.SERIAL_PORT,
            "SERIAL_BAUD": self.conf.SERIAL_BAUD,
            "SERIAL_ENCRYPTED": self.conf.SERIAL_ENCRYPTED,
            "PASSWORD": getattr(self.conf, "PASSWORD", None),
        }

    def update_serial_config(self, value):
        validated = validate_serial_config(value)
        self._persist_serial_config(validated)
        future = asyncio.run_coroutine_threadsafe(
            self.alarm.reconfigure_connection(validated), self._main_loop
        )
        future.add_done_callback(self._log_reconfigure_result)
        return {
            "accepted": True,
            "reconnect_scheduled": True,
            "config": deepcopy(validated),
        }

    @staticmethod
    def _log_reconfigure_result(future):
        try:
            future.result()
            logger.info("SafeLink serial configuration applied; reconnect scheduled")
        except Exception:
            logger.exception("Failed to apply SafeLink serial configuration")

    def _load_serial_config(self):
        path = SERIAL_CONFIG_FILE
        try:
            with path.open("r", encoding="utf-8") as stream:
                loaded = validate_serial_config(json.load(stream))
        except FileNotFoundError:
            return
        except (OSError, ValueError, json.JSONDecodeError):
            logger.exception("Ignoring invalid SafeLink serial configuration file")
            return

        for key, value in loaded.items():
            setattr(self.conf, key, value)
        logger.info("Loaded persisted SafeLink serial configuration")

    def _persist_serial_config(self, value: Mapping[str, Any]):
        target = SERIAL_CONFIG_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(target.parent),
                prefix=target.name + ".",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, SERIAL_CONFIG_FILE_MODE)
            os.replace(temp_name, target)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
