"""Sensors for Gree+ units."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfFrequency, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GreePlusConfigEntry
from .coordinator import GreeDeviceCoordinator
from .entity import GreeEntity
from .protocol import offset_temperature

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GreeSensorDescription(SensorEntityDescription):
    """Describes a sensor derived from one unit property."""

    prop: str
    convert: Callable[[Any], float | None] = offset_temperature


SENSORS: tuple[GreeSensorDescription, ...] = (
    GreeSensorDescription(
        key="indoor_temperature",
        translation_key="indoor_temperature",
        prop="TemSen",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    GreeSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        prop="OutEnvTem",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    GreeSensorDescription(
        key="indoor_coil_temperature",
        translation_key="indoor_coil_temperature",
        prop="InEvaTem",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    GreeSensorDescription(
        key="compressor_frequency",
        translation_key="compressor_frequency",
        prop="CompressorFqy",
        convert=lambda v: v if isinstance(v, (int, float)) else None,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreePlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for every unit."""
    async_add_entities(
        GreeSensor(coordinator, description)
        for coordinator in entry.runtime_data.coordinators.values()
        for description in SENSORS
    )


class GreeSensor(GreeEntity, SensorEntity):
    """A numeric reading reported by the unit."""

    entity_description: GreeSensorDescription

    def __init__(self, coordinator: GreeDeviceCoordinator, description: GreeSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.convert((self.coordinator.data or {}).get(self.entity_description.prop))
