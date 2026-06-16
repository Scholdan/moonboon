from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN
from .device import MoonboonDevice


class MoonboonEntity(Entity):
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, device: MoonboonDevice) -> None:
        self.entry = entry
        self.device = device

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.device.address)},
            name=self.device.name,
            manufacturer="Moonboon",
            model="Motor",
        )
