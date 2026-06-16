from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import MoonboonDevice
from .entity import MoonboonEntity
from .protocol import build_sequence_payload


@dataclass(frozen=True)
class NumberDescription:
    key: str
    name: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None


DESCRIPTIONS = (
    NumberDescription("speed", "Speed", 1, 100, 1),
    NumberDescription("duration", "Duration", 1, 720, 1, UnitOfTime.MINUTES),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: MoonboonDevice = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [MoonboonNumber(entry, device, description) for description in DESCRIPTIONS]
    )


class MoonboonNumber(MoonboonEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _remove_listener: Callable[[], None] | None = None

    def __init__(
        self,
        entry: ConfigEntry,
        device: MoonboonDevice,
        description: NumberDescription,
    ) -> None:
        super().__init__(entry, device)
        self.description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{device.address}_{description.key}"
        self._attr_native_min_value = description.minimum
        self._attr_native_max_value = description.maximum
        self._attr_native_step = description.step
        self._attr_native_unit_of_measurement = description.unit

    @property
    def native_value(self) -> float:
        return getattr(self.device, self.description.key)

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.device.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    async def async_set_native_value(self, value: float) -> None:
        setattr(self.device, self.description.key, int(value))
        if self.device.is_running:
            sequence = build_sequence_payload(
                self.device.speed,
                self.device.duration,
                self.device.fade_out,
                self.device.fade_steps,
            )
            await self.device.send_payloads("set_program", [sequence], keep_connected=False)
            if self.description.key == "duration":
                self.device.mark_running()
        self.async_write_ha_state()
