from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PAYLOADS
from .device import MoonboonDevice
from .entity import MoonboonEntity
from .protocol import build_sequence_payload


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    device: MoonboonDevice = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MoonboonRunSwitch(entry, device), MoonboonFadeOutSwitch(entry, device)])


class MoonboonRunSwitch(MoonboonEntity, SwitchEntity):
    _attr_name = None
    _remove_listener: Callable[[], None] | None = None


    def __init__(self, entry: ConfigEntry, device: MoonboonDevice) -> None:
        super().__init__(entry, device)
        self._attr_unique_id = f"{device.address}_switch"

    @property
    def is_on(self) -> bool:
        return self.device.is_running

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.device.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    async def async_turn_on(self, **kwargs) -> None:
        sequence = build_sequence_payload(
            self.device.speed,
            self.device.duration,
            self.device.fade_out,
            self.device.fade_steps,
        )
        await self.device.send_payloads(
            "run_program",
            [PAYLOADS["restart"], sequence, PAYLOADS["start"]],
            keep_connected=False,
        )
        self.device.mark_running()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.device.send_payloads("stop", [PAYLOADS["stop"]], keep_connected=False)
        self.device.mark_stopped()
        self.async_write_ha_state()


class MoonboonFadeOutSwitch(MoonboonEntity, SwitchEntity):
    _attr_name = "Fade Out"

    def __init__(self, entry: ConfigEntry, device: MoonboonDevice) -> None:
        super().__init__(entry, device)
        self._attr_unique_id = f"{device.address}_fade_out"

    @property
    def is_on(self) -> bool:
        return self.device.fade_out_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.device.fade_out_enabled = True
        await self._maybe_update_running_program()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.device.fade_out_enabled = False
        await self._maybe_update_running_program()
        self.async_write_ha_state()

    async def _maybe_update_running_program(self) -> None:
        if not self.device.is_running:
            return
        sequence = build_sequence_payload(
            self.device.speed,
            self.device.duration,
            self.device.fade_out,
            self.device.fade_steps,
        )
        await self.device.send_payloads("set_program", [sequence], keep_connected=False)
