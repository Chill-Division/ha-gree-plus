"""Diagnostics for Gree+."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import GreePlusConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, "key", "token"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GreePlusConfigEntry) -> dict[str, Any]:
    """Return redacted account and unit state."""
    data = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "broker_connected": data.client.connected,
        "units": {
            mac: {
                "device": async_redact_data(asdict(c.device), TO_REDACT),
                "online": c.online,
                "last_update_success": c.last_update_success,
                "state": c.data,
            }
            for mac, c in data.coordinators.items()
        },
    }
