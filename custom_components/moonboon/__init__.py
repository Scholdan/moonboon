from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DEVICE_NAME,
    DOMAIN,
    PAYLOADS,
    POLL_INTERVAL_SECONDS,
    PROGRAM_SERVICES,
)
from .device import MoonboonDevice
from .protocol import build_sequence_payload

PLATFORMS = (Platform.SWITCH, Platform.NUMBER, Platform.SENSOR)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def handle_command(call: ServiceCall) -> None:
        command = call.service
        device = _device_from_call(hass, call)
        payloads = [PAYLOADS[command]]
        if command == "start":
            payloads.insert(0, PAYLOADS["restart"])
        await device.send_payloads(command, payloads, keep_connected=False)
        if command == "start":
            device.mark_running()
        elif command == "stop":
            device.mark_stopped()

    async def handle_program(call: ServiceCall) -> None:
        service = call.service
        device = _device_from_call(hass, call)
        speed = int(call.data.get("speed", device.speed))
        duration = int(call.data.get("duration", device.duration))
        fade_out_enabled = _as_bool(call.data.get("fade_out", device.fade_out_enabled))
        fade_out = duration if fade_out_enabled else 0
        fade_steps = int(call.data.get("fade_steps", device.fade_steps))
        sequence = build_sequence_payload(speed, duration, fade_out, fade_steps)
        payloads = [sequence]
        if service == "run_program":
            payloads = [PAYLOADS["restart"], sequence, PAYLOADS["start"]]
        await device.send_payloads(service, payloads, keep_connected=False)
        # Persist only the fields this call named: defaults captured before the
        # BLE await must not clobber concurrent entity changes.
        duration_changed = duration != device.duration
        if "speed" in call.data:
            device.speed = speed
        if "duration" in call.data:
            device.duration = duration
        if "fade_out" in call.data:
            device.fade_out_enabled = fade_out_enabled
        if "fade_steps" in call.data:
            device.fade_steps = fade_steps
        if service == "run_program" or (device.is_running and duration_changed):
            device.mark_running()
        else:
            device.notify_listeners()

    for service in ("start", "stop"):
        hass.services.async_register(DOMAIN, service, handle_command)
    for service in PROGRAM_SERVICES:
        hass.services.async_register(DOMAIN, service, handle_program)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address = entry.data[CONF_ADDRESS].upper()
    name = entry.data.get(CONF_NAME, DEVICE_NAME)
    device = MoonboonDevice(hass, address, name)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = device

    async def poll_state(_now) -> None:
        try:
            await device.send_payloads(
                "check_state",
                [PAYLOADS["check_state"]],
                keep_connected=False,
                force_reconnect=True,
            )
        except Exception as err:
            _LOGGER.debug("Moonboon polling failed for %s: %s", device.address, err)

    async def update_countdown(_now) -> None:
        await device.update_countdown()

    entry.async_on_unload(
        async_track_time_interval(
            hass, poll_state, timedelta(seconds=POLL_INTERVAL_SECONDS)
        )
    )
    entry.async_on_unload(
        async_track_time_interval(hass, update_countdown, timedelta(seconds=60))
    )
    entry.async_on_unload(lambda: hass.async_create_task(device.disconnect()))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


def _device_from_call(hass: HomeAssistant, call: ServiceCall) -> MoonboonDevice:
    address = call.data.get(CONF_ADDRESS)
    devices: dict[str, MoonboonDevice] = hass.data.setdefault(DOMAIN, {})
    if address:
        wanted = address.upper()
        for device in devices.values():
            if device.address == wanted:
                return device
        raise HomeAssistantError(f"No configured Moonboon with address {wanted}")
    if len(devices) == 1:
        return next(iter(devices.values()))
    if devices:
        raise HomeAssistantError(
            "Multiple Moonboon devices are configured; pass an address"
        )
    raise HomeAssistantError("No Moonboon devices are configured")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)
