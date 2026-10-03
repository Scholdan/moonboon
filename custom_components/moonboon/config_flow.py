from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.exceptions import HomeAssistantError

from .const import DEVICE_NAME, DOMAIN
from .device import MoonboonDevice, MoonboonPairingUnsupported

_LOGGER = logging.getLogger(__name__)


class MoonboonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}
        self._pending: dict[str, Any] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        address = discovery_info.address.upper()
        name = discovery_info.name or discovery_info.advertisement.local_name or DEVICE_NAME

        _LOGGER.info("Discovered Moonboon %s at %s", name, address)
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        display_name = f"{name} ({address})"
        self._discovered = {CONF_ADDRESS: address, CONF_NAME: name}
        self.context["title_placeholders"] = {
            CONF_NAME: display_name,
            CONF_ADDRESS: address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input.get(
                CONF_ADDRESS, self._discovered.get(CONF_ADDRESS, "")
            ).upper()
            name = user_input.get(CONF_NAME, self._discovered.get(CONF_NAME, DEVICE_NAME))
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._pending = {CONF_ADDRESS: address, CONF_NAME: name}
            return await self.async_step_pair_confirm()

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS,
                        default=self._discovered.get(CONF_ADDRESS, ""),
                    ): str,
                    vol.Required(
                        CONF_NAME,
                        default=self._discovered.get(CONF_NAME, DEVICE_NAME),
                    ): str,
                }
            ),
            description_placeholders={
                CONF_NAME: self._discovered.get(CONF_NAME, DEVICE_NAME),
                CONF_ADDRESS: self._discovered.get(CONF_ADDRESS, "unknown"),
            },
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].upper()
            name = user_input.get(CONF_NAME, DEVICE_NAME)
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._pending = {CONF_ADDRESS: address, CONF_NAME: name}
            return await self.async_step_pair_confirm()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): str,
                    vol.Optional(CONF_NAME, default=DEVICE_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_pair_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        address = self._pending.get(CONF_ADDRESS, "")
        name = self._pending.get(CONF_NAME, DEVICE_NAME)

        if user_input is not None:
            if not user_input.get("pair_button_pressed"):
                errors["base"] = "pair_required"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                try:
                    await self._async_pair_device(address, name)
                except MoonboonPairingUnsupported:
                    errors["base"] = "pairing_unsupported"
                except TimeoutError:
                    errors["base"] = "not_found"
                except HomeAssistantError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Moonboon pairing failed for %s", address)
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=f"{name} ({address})",
                        data={CONF_ADDRESS: address, CONF_NAME: name},
                    )

        return self.async_show_form(
            step_id="pair_confirm",
            data_schema=vol.Schema(
                {vol.Required("pair_button_pressed", default=False): bool}
            ),
            description_placeholders={CONF_NAME: name, CONF_ADDRESS: address},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        entry = self._get_reauth_entry()
        self._pending = {
            CONF_ADDRESS: entry.data[CONF_ADDRESS],
            CONF_NAME: entry.data.get(CONF_NAME, DEVICE_NAME),
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        address = self._pending[CONF_ADDRESS]
        name = self._pending[CONF_NAME]
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("pair_button_pressed"):
                errors["base"] = "pair_required"
            else:
                try:
                    await self._async_pair_device(address, name)
                except MoonboonPairingUnsupported:
                    errors["base"] = "pairing_unsupported"
                except TimeoutError:
                    errors["base"] = "not_found"
                except HomeAssistantError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Moonboon re-pairing failed for %s", address)
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_update_reload_and_abort(self._get_reauth_entry())
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required("pair_button_pressed", default=False): bool}
            ),
            description_placeholders={CONF_NAME: name, CONF_ADDRESS: address},
            errors=errors,
        )

    async def _async_pair_device(self, address: str, name: str) -> None:
        """Wait for a fresh pairing-mode advertisement before bonding."""
        await bluetooth.async_process_advertisements(
            self.hass,
            lambda _info: True,
            {"address": address, "connectable": True},
            BluetoothScanningMode.ACTIVE,
            15,
        )
        await MoonboonDevice(self.hass, address, name).pair()
