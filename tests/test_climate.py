"""Tests for setup, the climate entity and live updates."""

from dataclasses import replace

from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_SWING_HORIZONTAL_MODE,
    ATTR_SWING_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_SWING_HORIZONTAL_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gree_plus.api import GreeAuthError
from custom_components.gree_plus.protocol import STATUS_COLS

from .conftest import DEVICE, MAC

ENTITY = "climate.nursery_mezz_ac"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY, **data}, blocking=True)


async def test_setup_reports_state(hass: HomeAssistant, fake_broker, config_entry) -> None:
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert fake_broker.instance.host == "mqtt-au.gree.com"

    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.AUTO
    assert state.attributes[ATTR_TEMPERATURE] == 19
    assert state.attributes["current_temperature"] == 20
    assert state.attributes[ATTR_FAN_MODE] == "auto"
    assert "turbo" not in state.attributes[ATTR_FAN_MODES]
    assert state.attributes[ATTR_SWING_MODE] == "off"
    assert state.attributes[ATTR_SWING_HORIZONTAL_MODE] == "off"
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE

    assert hass.states.get("sensor.nursery_mezz_ac_indoor_temperature").state == "20"
    assert hass.states.get("sensor.nursery_mezz_ac_outdoor_temperature").state == "18"
    assert hass.states.get("sensor.nursery_mezz_ac_compressor_frequency").state == "0"

    # The first poll asks for every property the entities use.
    assert fake_broker.instance.sent[0] == (MAC, {"t": "status", "mac": MAC, "cols": STATUS_COLS})


async def test_auth_failure_starts_reauth(hass: HomeAssistant, fake_broker, fake_cloud, config_entry) -> None:
    fake_cloud.fail_with = GreeAuthError("bad password")
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())


@pytest.mark.parametrize(
    ("service", "data", "expected"),
    [
        (SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: 21.4}, {"SetTem": 21, "TemUn": 0}),
        (SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: 22, ATTR_HVAC_MODE: HVACMode.COOL}, None),
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.COOL}, {"Mod": 1, "Pow": 1}),
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.DRY}, {"Mod": 2, "Pow": 1}),
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.FAN_ONLY}, {"Mod": 3, "Pow": 1}),
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.HEAT}, {"Mod": 4, "Pow": 1}),
        (SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: HVACMode.OFF}, {"Pow": 0}),
        (SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "medium_low"}, {"WdSpd": 2, "Tur": 0, "Quiet": 0}),
        (SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: "quiet"}, {"Quiet": 2, "Tur": 0}),
        (SERVICE_SET_SWING_MODE, {ATTR_SWING_MODE: "fixed_bottom"}, {"SwUpDn": 6}),
        (SERVICE_SET_SWING_MODE, {ATTR_SWING_MODE: "swing"}, {"SwUpDn": 1}),
        (SERVICE_SET_SWING_HORIZONTAL_MODE, {ATTR_SWING_HORIZONTAL_MODE: "fixed_left"}, {"SwingLfRig": 2}),
        (SERVICE_TURN_OFF, {}, {"Pow": 0}),
    ],
)
async def test_commands(hass: HomeAssistant, fake_broker, config_entry, service, data, expected) -> None:
    await _setup(hass, config_entry)
    await _call(hass, service, **data)
    sent = fake_broker.instance.commands()
    if expected is None:  # temperature + mode in one call
        expected = {"Mod": 1, "Pow": 1, "SetTem": 22, "TemUn": 0}
    assert sent == [expected]
    # The acknowledged values are reflected straight away.
    state = hass.states.get(ENTITY)
    if "SetTem" in expected:
        assert state.attributes[ATTR_TEMPERATURE] == expected["SetTem"]
    if expected.get("Pow") == 0:
        assert state.state == HVACMode.OFF


async def test_turbo_only_in_cool_and_heat(hass: HomeAssistant, fake_broker, config_entry) -> None:
    await _setup(hass, config_entry)
    await _call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.COOL)
    state = hass.states.get(ENTITY)
    assert "turbo" in state.attributes[ATTR_FAN_MODES]

    await _call(hass, SERVICE_SET_FAN_MODE, fan_mode="turbo")
    assert fake_broker.instance.commands()[-1] == {"Tur": 1, "Quiet": 0}
    assert hass.states.get(ENTITY).attributes[ATTR_FAN_MODE] == "turbo"


async def test_turn_on(hass: HomeAssistant, fake_broker, config_entry) -> None:
    await _setup(hass, config_entry)
    await _call(hass, SERVICE_TURN_OFF)
    await _call(hass, SERVICE_TURN_ON)
    assert fake_broker.instance.commands() == [{"Pow": 0}, {"Pow": 1}]
    assert hass.states.get(ENTITY).state == HVACMode.AUTO


async def test_push_updates(hass: HomeAssistant, fake_broker, config_entry) -> None:
    """Live status pushes update the entity; retained snapshots are ignored as stale."""
    await _setup(hass, config_entry)
    client = fake_broker.instance

    client.push(MAC, "status", {"t": "dat", "cols": ["SetTem", "Pow"], "dat": [25, 1]}, retained=True)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes[ATTR_TEMPERATURE] == 19

    client.push(
        MAC,
        "status",
        {"t": "dat", "cols": ["Mod", "CompressorFqy", "InEvaTem", "TemSen"], "dat": [0, 40, 50, 60]},
    )
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes[ATTR_HVAC_ACTION] == HVACAction.COOLING

    client.push(MAC, "status", {"t": "dat", "cols": ["Mod"], "dat": [4]})
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING


async def test_offline_notice(hass: HomeAssistant, fake_broker, config_entry) -> None:
    await _setup(hass, config_entry)
    client = fake_broker.instance

    client.push(MAC, "connect", {"t": "connect", "online": 0})
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE

    client.push(MAC, "connect", {"t": "connect", "online": 1})
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == HVACMode.AUTO


async def test_unreachable_unit_is_unavailable(hass: HomeAssistant, fake_broker, fake_cloud, config_entry) -> None:
    """A unit that never answers doesn't block setup; it just shows unavailable."""

    original_register = fake_broker.register

    def register(self, mac, key, handler):
        original_register(self, mac, key, handler)
        self.unreachable = True

    fake_broker.register = register
    try:
        await _setup(hass, config_entry)
    finally:
        fake_broker.register = original_register
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE


async def test_repaired_unit_gets_new_key(hass: HomeAssistant, fake_broker, fake_cloud, config_entry) -> None:
    """Undecodable live messages trigger a key refresh from the cloud."""
    await _setup(hass, config_entry)
    client = fake_broker.instance
    fake_cloud.devices = [replace(DEVICE, key="fedcba9876543210")]
    calls = fake_cloud.get_devices_calls

    client.on_bad_key(MAC)
    await hass.async_block_till_done()

    assert fake_cloud.get_devices_calls == calls + 1
    assert client.keys[MAC] == "fedcba9876543210"

    # Further bad messages within the cooldown don't hammer the API.
    client.on_bad_key(MAC)
    await hass.async_block_till_done()
    assert fake_cloud.get_devices_calls == calls + 1


async def test_unload(hass: HomeAssistant, fake_broker, config_entry) -> None:
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert not fake_broker.instance.connected


async def test_diagnostics_are_redacted(hass: HomeAssistant, fake_broker, config_entry) -> None:
    from custom_components.gree_plus.diagnostics import async_get_config_entry_diagnostics

    await _setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diag)
    assert "hunter2" not in text and DEVICE.key not in text and "grower@example.com" not in text
    assert diag["units"][MAC]["state"]["SetTem"] == 19
