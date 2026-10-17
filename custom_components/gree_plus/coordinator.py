"""Per-unit state: polled as a fallback, updated live from pushed status messages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CloudDevice
from .cloud_mqtt import GreeMqttClient, GreeRequestError
from .const import DOMAIN, LOGGER, POLL_INTERVAL
from .protocol import command_payload, state_from_payload, status_payload


class GreeDeviceCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds the latest known properties of one unit."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: GreeMqttClient,
        device: CloudDevice,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.mac}",
            update_interval=POLL_INTERVAL,
        )
        self.client = client
        self.device = device
        self.online = True
        self.request_key_refresh: Callable[[], None] | None = None
        client.register(device.mac, device.key, self._handle_message)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            reply = await self.client.request(self.device.mac, status_payload(self.device.mac))
        except GreeRequestError as err:
            # A unit that was re-paired ignores requests sealed with its old key.
            if self.request_key_refresh is not None:
                self.request_key_refresh()
            raise UpdateFailed(str(err)) from err
        state = state_from_payload(reply)
        if not state:
            raise UpdateFailed(f"unexpected status reply: {reply}")
        self.online = True
        return {**(self.data or {}), **state}

    async def async_send(self, values: dict[str, int]) -> None:
        """Set one or more properties and record what the unit acknowledged."""
        try:
            reply = await self.client.request(self.device.mac, command_payload(self.device.mac, values))
        except GreeRequestError as err:
            raise HomeAssistantError(f"{self.device.name}: {err}") from err
        if reply.get("r") != 200:
            raise HomeAssistantError(f"{self.device.name} rejected {values}: {reply}")
        acked = state_from_payload(reply) or values
        self.async_set_updated_data({**(self.data or {}), **acked})

    @callback
    def _handle_message(self, kind: str, payload: dict[str, Any], retained: bool) -> None:
        if kind == "connect":
            online = bool(payload.get("online", 1))
            if online != self.online:
                LOGGER.info("%s is now %s", self.device.name, "online" if online else "offline")
                self.online = online
                self.async_update_listeners()
            return
        # Retained snapshots can be weeks old; the first poll supplies fresh state.
        if retained:
            return
        state = state_from_payload(payload)
        if state:
            self.online = True
            self.async_set_updated_data({**(self.data or {}), **state})
