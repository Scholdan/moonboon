from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CHARACTERISTIC_UUID, DEVICE_NAME, SERVICE_UUID
from .protocol import decode_payload

_LOGGER = logging.getLogger(__name__)


def _is_int(value: Any) -> bool:
    # bool subclasses int; a CBOR false must not read as remaining == 0.
    return isinstance(value, int) and not isinstance(value, bool)


class MoonboonDevice:
    def __init__(self, hass: HomeAssistant, address: str, name: str = DEVICE_NAME) -> None:
        self.hass = hass
        self.address = address.upper()
        self.name = name
        self.speed = 50
        self.duration = 60
        self.fade_out_enabled = False
        self.fade_steps = 12
        self.is_running = False
        self.state = "stopped"
        self.remaining: int = 0
        self.remaining_total: int = 0
        self.run_started_at: float | None = None
        self.last_decoded: object | None = None
        self.last_raw_notification: str | None = None
        self._client: Any | None = None
        self._notify_started = False
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    @property
    def fade_out(self) -> int:
        return self.duration if self.fade_out_enabled else 0

    @property
    def duration_seconds(self) -> int:
        return self.duration * 60

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    @property
    def _run_total(self) -> int:
        # Device-reported program length beats the locally configured duration
        # for runs started outside Home Assistant.
        return self.remaining_total or self.duration_seconds

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    def mark_running(self) -> None:
        self.is_running = True
        self.state = "running"
        self.remaining = self.duration_seconds
        self.remaining_total = self.duration_seconds
        self.run_started_at = time.time()
        self.notify_listeners()

    def mark_stopped(self) -> None:
        self.is_running = False
        self.state = "stopped"
        self.remaining = 0
        self.run_started_at = None
        self.notify_listeners()

    async def update_countdown(self) -> None:
        if not self.is_running or self.run_started_at is None:
            return
        remaining = max(0, self._run_total - int(time.time() - self.run_started_at))
        if remaining != self.remaining:
            self.remaining = remaining
            self.notify_listeners()
        if remaining == 0:
            self.mark_stopped()
            await self.disconnect()

    def _handle_notification(self, raw: bytes) -> None:
        self.last_raw_notification = raw.hex(" ")
        try:
            decoded = decode_payload(raw)
        except Exception as err:
            _LOGGER.debug("Could not decode Moonboon notification %s: %s", raw.hex(" "), err)
            return
        self.last_decoded = decoded
        _LOGGER.debug("Moonboon decoded notification: %r", decoded)
        if not isinstance(decoded, dict):
            return
        changed = False
        state = decoded.get("state")
        if isinstance(state, str):
            self.state = state
            self.is_running = state == "running"
            if self.is_running and self.run_started_at is None:
                self.run_started_at = time.time()
            if not self.is_running:
                self.remaining = 0
                self.run_started_at = None
                self.hass.async_create_task(self.disconnect())
            changed = True
        if _is_int(decoded.get("remaining total")):
            self.remaining_total = decoded["remaining total"]
            changed = True
        if _is_int(decoded.get("remaining")):
            self.remaining = decoded["remaining"]
            if self.is_running:
                self.run_started_at = time.time() - max(0, self._run_total - self.remaining)
            if self.remaining == 0:
                self.is_running = False
                self.state = "stopped"
                self.run_started_at = None
                self.hass.async_create_task(self.disconnect())
            changed = True
        # Notification duration appears to be runtime/status data, not the configured timer.
        if changed:
            self.notify_listeners()

    async def ensure_connected(self, action: str) -> None:
        if self._client and self._client.is_connected and self._notify_started:
            return

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"Moonboon {self.address} is not visible to Home Assistant Bluetooth"
            )

        _LOGGER.debug(
            "Connecting to Moonboon %s for %s via %s",
            self.address,
            action,
            getattr(ble_device, "details", "unknown adapter/proxy"),
        )

        def notification_handler(_sender: int, data: bytearray) -> None:
            raw = bytes(data)
            _LOGGER.debug("Moonboon notification: %s", raw.hex(" "))
            self._handle_notification(raw)

        self._client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.name,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            ),
        )

        services = self._client.services
        if services is None and hasattr(self._client, "get_services"):
            services = await self._client.get_services()
        if services is None:
            raise HomeAssistantError("Moonboon GATT services were not available")
        if services.get_service(SERVICE_UUID) is None:
            raise HomeAssistantError(
                f"Moonboon service {SERVICE_UUID} was not discovered on {self.address}"
            )
        if services.get_characteristic(CHARACTERISTIC_UUID) is None:
            raise HomeAssistantError(
                f"Moonboon characteristic {CHARACTERISTIC_UUID} was not discovered on {self.address}"
            )

        await self._client.start_notify(CHARACTERISTIC_UUID, notification_handler)
        self._notify_started = True

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            client = self._client
            self._client = None
            self._notify_started = False
            if not client.is_connected:
                return
            try:
                await client.stop_notify(CHARACTERISTIC_UUID)
            except Exception as err:
                _LOGGER.debug("Moonboon stop_notify failed for %s: %s", self.address, err)
            await client.disconnect()

    async def send_payloads(
        self,
        action: str,
        payloads: list[bytes],
        keep_connected: bool | None = None,
        force_reconnect: bool = False,
    ) -> None:
        if keep_connected is None:
            keep_connected = False

        if force_reconnect:
            await self.disconnect()
        if action == "check_state":
            self.last_raw_notification = None
            self.last_decoded = None

        try:
            notification_count = 0
            async with self._lock:
                await self.ensure_connected(action)
                client = self._client
                if client is None:
                    raise HomeAssistantError("Moonboon BLE client was not available")

                wrote_any = False
                for payload in payloads:
                    _LOGGER.debug(
                        "Writing Moonboon %s payload: %s", action, payload.hex(" ")
                    )
                    await client.write_gatt_char(CHARACTERISTIC_UUID, payload, response=False)
                    wrote_any = True
                    await asyncio.sleep(0.2)

                if not wrote_any:
                    await asyncio.sleep(1)
                elif not keep_connected:
                    await asyncio.sleep(2)

                notification_count = 1 if self.last_raw_notification else 0

            if action == "check_state":
                _LOGGER.debug(
                    "Moonboon check_state result for %s: notifications_seen=%s last_raw=%s last_decoded=%r running=%s remaining=%s",
                    self.address,
                    notification_count,
                    self.last_raw_notification,
                    self.last_decoded,
                    self.is_running,
                    self.remaining,
                )

        except Exception as err:
            raise HomeAssistantError(
                f"Moonboon {action} failed before/during BLE write: {err}. "
                "If this is ESP_GATT_CONN_FAIL_ESTABLISH, move the ESPHome proxy closer, "
                "disable the phone Bluetooth/Moonboon app, and confirm bluetooth_proxy active connections are enabled."
            ) from err
        finally:
            if not keep_connected:
                await self.disconnect()
