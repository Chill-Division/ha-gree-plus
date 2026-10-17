"""The Gree+ integration: Gree air conditioners that are controlled through the Gree+ cloud."""

from __future__ import annotations

from dataclasses import dataclass
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_context

from .api import MQTT_PORT, REGIONS, GreeApiError, GreeAuthError, GreeCloudApi
from .cloud_mqtt import GreeMqttClient, GreeRequestError
from .const import CONF_REGION, KEY_REFRESH_COOLDOWN, LOGGER
from .coordinator import GreeDeviceCoordinator

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]


@dataclass
class GreePlusData:
    """Runtime objects for one Gree+ account."""

    api: GreeCloudApi
    client: GreeMqttClient
    coordinators: dict[str, GreeDeviceCoordinator]


type GreePlusConfigEntry = ConfigEntry[GreePlusData]


async def async_setup_entry(hass: HomeAssistant, entry: GreePlusConfigEntry) -> bool:
    """Log in, discover the account's units and connect to the broker."""
    region = entry.data[CONF_REGION]
    api = GreeCloudApi(
        async_get_clientsession(hass),
        region,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    try:
        uid = await api.login()
        devices = await api.get_devices()
    except GreeAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GreeApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    async def refresh_token() -> str:
        await api.login()
        assert api.token is not None
        return api.token

    assert api.token is not None
    client = GreeMqttClient(
        hass.loop,
        REGIONS[region][1],
        MQTT_PORT,
        uid,
        api.token,
        get_default_context(),
        refresh_token,
    )
    coordinators = {dev.mac: GreeDeviceCoordinator(hass, entry, client, dev) for dev in devices}

    last_key_refresh = 0.0

    async def refresh_keys() -> None:
        """Re-read device keys: a unit's key changes every time it is re-paired in the app."""
        nonlocal last_key_refresh
        if time.monotonic() - last_key_refresh < KEY_REFRESH_COOLDOWN:
            return
        last_key_refresh = time.monotonic()
        try:
            await api.login()
            fresh = await api.get_devices()
        except GreeApiError as err:
            LOGGER.warning("Could not refresh Gree+ device keys: %s", err)
            return
        for dev in fresh:
            coordinator = coordinators.get(dev.mac)
            if coordinator is None:
                LOGGER.info("New Gree+ unit %s found; reloading", dev.name)
                hass.config_entries.async_schedule_reload(entry.entry_id)
                return
            if dev.key != coordinator.device.key:
                LOGGER.info("%s was re-paired; using its new key", dev.name)
                client.set_key(dev.mac, dev.key)
                coordinator.device = dev
                await coordinator.async_request_refresh()

    def request_key_refresh(_mac: str | None = None) -> None:
        entry.async_create_background_task(hass, refresh_keys(), "gree_plus key refresh")

    client.on_bad_key = request_key_refresh
    for coordinator in coordinators.values():
        coordinator.request_key_refresh = request_key_refresh
    try:
        await client.start()
    except GreeRequestError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.async_on_unload(client.stop)

    # A unit that is offline right now should show as unavailable, not block setup.
    for coordinator in coordinators.values():
        await coordinator.async_refresh()

    entry.runtime_data = GreePlusData(api, client, coordinators)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GreePlusConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
