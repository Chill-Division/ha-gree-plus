"""Climate entity for Gree+ units."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GreePlusConfigEntry
from .entity import GreeEntity
from .protocol import (
    HORIZONTAL_SWING,
    MAX_TEMP,
    MIN_TEMP,
    VERTICAL_SWING,
    FanSpeed,
    Mode,
    offset_temperature,
)

PARALLEL_UPDATES = 1

HVAC_MODES = {
    Mode.AUTO: HVACMode.AUTO,
    Mode.COOL: HVACMode.COOL,
    Mode.DRY: HVACMode.DRY,
    Mode.FAN: HVACMode.FAN_ONLY,
    Mode.HEAT: HVACMode.HEAT,
}
GREE_MODES = {hvac: mode for mode, hvac in HVAC_MODES.items()}

FAN_SPEEDS = {
    "auto": FanSpeed.AUTO,
    "low": FanSpeed.LOW,
    "medium_low": FanSpeed.MEDIUM_LOW,
    "medium": FanSpeed.MEDIUM,
    "medium_high": FanSpeed.MEDIUM_HIGH,
    "high": FanSpeed.HIGH,
}
FAN_QUIET = "quiet"
FAN_TURBO = "turbo"

VERTICAL_BY_VALUE = {v: k for k, v in VERTICAL_SWING.items()}
HORIZONTAL_BY_VALUE = {v: k for k, v in HORIZONTAL_SWING.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreePlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a climate entity per unit."""
    async_add_entities(GreeClimate(c) for c in entry.runtime_data.coordinators.values())


class GreeClimate(GreeEntity, ClimateEntity):
    """A Gree+ air conditioner."""

    _attr_name = None
    _attr_translation_key = "gree_plus"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_hvac_modes = [HVACMode.OFF, *HVAC_MODES.values()]
    _attr_swing_modes = list(VERTICAL_SWING)
    _attr_swing_horizontal_modes = list(HORIZONTAL_SWING)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.SWING_HORIZONTAL_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "climate")

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self._state.get("Pow"):
            return HVACMode.OFF
        return HVAC_MODES.get(self._state.get("Mod"))

    @property
    def hvac_action(self) -> HVACAction | None:
        state = self._state
        if not state.get("Pow"):
            return HVACAction.OFF
        mode = state.get("Mod")
        if mode == Mode.FAN:
            return HVACAction.FAN
        if not state.get("CompressorFqy"):
            return HVACAction.IDLE
        if mode == Mode.COOL:
            return HVACAction.COOLING
        if mode == Mode.DRY:
            return HVACAction.DRYING
        if mode == Mode.HEAT:
            return HVACAction.HEATING
        # Auto: the indoor coil runs colder than the room when cooling, warmer when heating.
        coil, room = offset_temperature(state.get("InEvaTem")), offset_temperature(state.get("TemSen"))
        if coil is None or room is None:
            return None
        return HVACAction.COOLING if coil < room else HVACAction.HEATING

    @property
    def current_temperature(self) -> float | None:
        return offset_temperature(self._state.get("TemSen"))

    @property
    def target_temperature(self) -> float | None:
        return self._state.get("SetTem")

    @property
    def fan_modes(self) -> list[str]:
        """Turbo is only offered in Cool and Heat, as in the Gree+ app."""
        if self._state.get("Mod") in (Mode.COOL, Mode.HEAT):
            return [*FAN_SPEEDS, FAN_QUIET, FAN_TURBO]
        return [*FAN_SPEEDS, FAN_QUIET]

    @property
    def fan_mode(self) -> str | None:
        state = self._state
        if state.get("Tur"):
            return FAN_TURBO
        if state.get("Quiet"):
            return FAN_QUIET
        speed = state.get("WdSpd")
        return next((name for name, value in FAN_SPEEDS.items() if value == speed), None)

    @property
    def swing_mode(self) -> str | None:
        return VERTICAL_BY_VALUE.get(self._state.get("SwUpDn"))

    @property
    def swing_horizontal_mode(self) -> str | None:
        return HORIZONTAL_BY_VALUE.get(self._state.get("SwingLfRig"))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_send({"Pow": 0})
        else:
            await self.coordinator.async_send({"Mod": int(GREE_MODES[hvac_mode]), "Pow": 1})

    async def async_turn_on(self) -> None:
        await self.coordinator.async_send({"Pow": 1})

    async def async_turn_off(self) -> None:
        await self.coordinator.async_send({"Pow": 0})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        values: dict[str, int] = {}
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            if hvac_mode == HVACMode.OFF:
                values["Pow"] = 0
            else:
                values.update({"Mod": int(GREE_MODES[hvac_mode]), "Pow": 1})
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            values.update({"SetTem": round(temperature), "TemUn": 0})
        if values:
            await self.coordinator.async_send(values)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if fan_mode == FAN_TURBO:
            await self.coordinator.async_send({"Tur": 1, "Quiet": 0})
        elif fan_mode == FAN_QUIET:
            # The Gree+ app sends Quiet=2 (1 is an older "quiet" level some units ignore).
            await self.coordinator.async_send({"Quiet": 2, "Tur": 0})
        else:
            await self.coordinator.async_send({"WdSpd": int(FAN_SPEEDS[fan_mode]), "Tur": 0, "Quiet": 0})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.coordinator.async_send({"SwUpDn": VERTICAL_SWING[swing_mode]})

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        await self.coordinator.async_send({"SwingLfRig": HORIZONTAL_SWING[swing_horizontal_mode]})
