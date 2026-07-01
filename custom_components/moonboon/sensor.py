from __future__ import annotations

import math
from collections.abc import Callable

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import MoonboonDevice
from .entity import MoonboonEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: MoonboonDevice = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MoonboonRemainingSensor(entry, device),
        ]
    )


class MoonboonSensor(MoonboonEntity, SensorEntity):
    _remove_listener: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.device.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None


class MoonboonRemainingSensor(MoonboonSensor):
    _attr_name = "Remaining"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, entry: ConfigEntry, device: MoonboonDevice) -> None:
        super().__init__(entry, device)
        self._attr_unique_id = f"{device.address}_remaining"

    @property
    def native_value(self) -> int:
        return math.ceil(self.device.remaining / 60)
