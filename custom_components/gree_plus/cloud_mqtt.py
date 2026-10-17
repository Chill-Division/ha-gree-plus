"""Connection to Gree's regional MQTT broker (the same one the Gree+ app uses)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import secrets
import ssl
from typing import Any

import paho.mqtt.client as mqtt

from .protocol import GENERIC_KEY, build_request, decrypt_pack, new_cid

_LOGGER = logging.getLogger(__name__)

# CONNACK reason codes that mean the token is no longer accepted.
_AUTH_FAILURES = {"Bad user name or password", "Not authorized"}

type MessageHandler = Callable[[str, dict[str, Any], bool], None]


class GreeRequestError(Exception):
    """A request to a unit failed or timed out."""


class GreeMqttClient:
    """One broker session per account, shared by all of the account's units."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        host: str,
        port: int,
        uid: int,
        token: str,
        ssl_context: ssl.SSLContext,
        refresh_token: Callable[[], Awaitable[str]],
    ) -> None:
        self._loop = loop
        self._host = host
        self._port = port
        self._uid = uid
        self._refresh_token = refresh_token
        self.on_bad_key: Callable[[str], None] | None = None
        self._keys: dict[str, bytes] = {}
        self._handlers: dict[str, MessageHandler] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._connected = asyncio.Event()
        self._refreshing = False
        self._closing = False

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"app_{secrets.token_hex(8)}",
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self._client.username_pw_set(str(uid), token)
        self._client.tls_set_context(ssl_context)
        self._client.reconnect_delay_set(min_delay=2, max_delay=120)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @property
    def connected(self) -> bool:
        """Whether the broker session is up."""
        return self._connected.is_set()

    def register(self, mac: str, key: str, handler: MessageHandler) -> None:
        """Route messages for ``mac`` to ``handler`` (call before ``start``)."""
        self._keys[mac] = key.encode()
        self._handlers[mac] = handler

    def set_key(self, mac: str, key: str) -> None:
        """Replace a unit's key (it changes whenever the unit is re-paired)."""
        self._keys[mac] = key.encode()

    async def start(self, timeout: float = 20) -> None:
        """Connect to the broker and wait for the first session."""
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()
        try:
            async with asyncio.timeout(timeout):
                await self._connected.wait()
        except TimeoutError as err:
            await self.stop()
            raise GreeRequestError(f"could not connect to {self._host}:{self._port}") from err

    async def stop(self) -> None:
        """Disconnect and stop the network thread."""
        self._closing = True
        self._client.disconnect()
        await self._loop.run_in_executor(None, self._client.loop_stop)
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def request(self, mac: str, payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
        """Send ``payload`` to a unit and return its decrypted reply."""
        if not self._connected.is_set():
            raise GreeRequestError("not connected to the Gree broker")
        cid = new_cid()
        fut: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        self._pending[cid] = fut
        try:
            info = self._client.publish(
                f"request/{mac}",
                build_request(mac, self._uid, self._keys[mac], payload, cid),
                qos=1,
            )
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise GreeRequestError(f"publish failed: {mqtt.error_string(info.rc)}")
            async with asyncio.timeout(timeout):
                return await fut
        except TimeoutError as err:
            raise GreeRequestError(f"no reply from {mac} within {timeout}s") from err
        finally:
            self._pending.pop(cid, None)

    # paho callbacks run on paho's network thread; hop onto the event loop.

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Any, reason: Any, props: Any) -> None:
        if reason.is_failure:
            _LOGGER.warning("Gree broker refused the connection: %s", reason)
            if str(reason) in _AUTH_FAILURES:
                self._loop.call_soon_threadsafe(self._schedule_token_refresh)
            return
        topics = []
        for mac in self._handlers:
            topics += [(f"response/{mac}/#", 1), (f"status/{mac}/#", 1), (f"connect/{mac}", 1)]
        if topics:
            client.subscribe(topics)
        _LOGGER.debug("Connected to %s, subscribed for %d unit(s)", self._host, len(self._handlers))
        self._loop.call_soon_threadsafe(self._connected.set)

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, flags: Any, reason: Any, props: Any) -> None:
        self._loop.call_soon_threadsafe(self._connected.clear)
        if not self._closing:
            _LOGGER.info("Disconnected from the Gree broker (%s); paho will reconnect", reason)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        self._loop.call_soon_threadsafe(self._dispatch, msg.topic, msg.payload, msg.retain)

    def _schedule_token_refresh(self) -> None:
        if self._refreshing or self._closing:
            return
        self._refreshing = True
        self._loop.create_task(self._do_token_refresh(), name="gree_plus token refresh")

    async def _do_token_refresh(self) -> None:
        try:
            token = await self._refresh_token()
        except Exception:
            _LOGGER.exception("Could not refresh the Gree+ login")
            return
        finally:
            self._refreshing = False
        self._client.username_pw_set(str(self._uid), token)
        _LOGGER.info("Refreshed Gree+ login; reconnecting")

    def _dispatch(self, topic: str, raw: bytes, retained: bool) -> None:
        parts = topic.split("/")
        if len(parts) < 2 or parts[1] not in self._handlers:
            return
        kind, mac = parts[0], parts[1]
        try:
            envelope = json.loads(raw)
            key = GENERIC_KEY if kind == "connect" else self._keys[mac]
            payload = decrypt_pack(envelope["pack"], key)
        except (ValueError, KeyError, TypeError) as err:
            _LOGGER.debug("Ignoring undecodable message on %s: %s", topic, err)
            # A live message we can't read usually means the unit was re-paired.
            if kind != "connect" and not retained and self.on_bad_key is not None:
                self.on_bad_key(mac)
            return

        if kind == "response":
            fut = self._pending.get(str(envelope.get("tcid", "")))
            if fut is not None and not fut.done():
                fut.set_result(payload)
                return
        self._handlers[mac](kind, payload, retained)
