from abc import abstractmethod
import logging

from paradox.connections.handler import ConnectionHandler
from paradox.connections.protocol_base import ConnectionProtocol
from paradox.lib.async_message_manager import AsyncMessageManager

logger = logging.getLogger("PAI").getChild(__name__)


class Connection(AsyncMessageManager, ConnectionHandler):
    _protocol: ConnectionProtocol

    def __init__(self):
        super().__init__()
        self.connected = False
        self._closing = False
        self._protocol: ConnectionProtocol = None

    def on_message(self, raw: bytes):
        self.schedule_raw_message_handling(raw)

    def on_connection(self):
        self._closing = False
        logger.info("Connection established")

    def on_connection_loss(self):
        if self._closing:
            logger.info("Connection closed by local shutdown")
        else:
            logger.error("Connection was lost")
        self.connected = False

    @property
    def connected(self) -> bool:
        return (
            self._connected
            and self._protocol is not None
            and self._protocol.is_active()
        )

    @connected.setter
    def connected(self, value: bool):
        self._connected = value

    @abstractmethod
    async def connect(self) -> bool:
        raise NotImplementedError("Implement in a subclass")

    def write(self, data: bytes):
        if self.connected:
            self._protocol.send_message(data)  # throws ConnectionError
        else:
            raise ConnectionError("Not connected")

    async def close(self):
        if self._protocol:
            self._closing = True
            try:
                await self._protocol.close()
            finally:
                self._protocol = None
        self.connected = False

    def variable_message_length(self, mode):
        if self._protocol is not None:
            self._protocol.variable_message_length(mode)
