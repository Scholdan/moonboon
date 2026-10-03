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

from .const import CHARACTERISTIC_UUID, DEVICE_NAME, PAYLOADS, SERVICE_UUID
from .protocol import SmpFrameReader, build_read_payload, with_sequence

_LOGGER = logging.getLogger(__name__)


def _is_int(value: Any) -> bool:
    # bool subclasses int; a CBOR false must not read as remaining == 0.
    return isinstance(value, int) and not isinstance(value, bool)


class MoonboonConnectionError(HomeAssistantError):
    """A BLE connection or request failed, rather than a motor command."""


class MoonboonPairingUnsupported(HomeAssistantError):
    """The active adapter or proxy cannot initiate bonding."""


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
        self._frames = SmpFrameReader()
        self._pending: tuple[int, int, int, asyncio.Future[dict]] | None = None
        self._sequence = 1

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

    def _apply_status(self, decoded: dict) -> None:
        """Apply a complete status response, never a command acknowledgement."""
        state = decoded.get("state")
        if isinstance(state, str):
            self.state = state
            self.is_running = state == "running"
            if self.is_running and self.run_started_at is None:
                self.run_started_at = time.time()
            if not self.is_running:
                self.remaining = 0
                self.run_started_at = None
        if _is_int(decoded.get("remaining total")):
            self.remaining_total = decoded["remaining total"]
        if _is_int(decoded.get("remaining")):
            self.remaining = decoded["remaining"] if self.is_running else 0
            if self.is_running:
                self.run_started_at = time.time() - max(
                    0, self._run_total - self.remaining
                )
        self.notify_listeners()

    def _handle_notification(self, raw: bytes) -> None:
        self.last_raw_notification = raw.hex(" ")
        for frame in self._frames.feed(raw):
            self.last_decoded = frame.payload
            _LOGGER.debug("Moonboon decoded notification: %r", frame.payload)
            if frame.command == 5 and frame.sequence == 0:
                if frame.payload.get("ur") == "ustop":
                    self.state = "stopped (user)"
                    self.is_running = False
                    self.remaining = 0
                    self.run_started_at = None
                    self.notify_listeners()
                continue
            pending = self._pending
            if pending and (frame.sequence, frame.op, frame.command) == pending[:3]:
                if not pending[3].done():
                    pending[3].set_result(frame.payload)

    async def ensure_connected(self, action: str) -> None:
        if self._client and self._client.is_connected and self._notify_started:
            return
        if self._client is not None:
            await self._disconnect_unlocked()

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise MoonboonConnectionError(
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

        self._frames.reset()
        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.name,
                ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                ),
                timeout=20,
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
        except BaseException:
            await self._disconnect_unlocked()
            raise

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect_unlocked()

    async def _disconnect_unlocked(self) -> None:
        client, self._client = self._client, None
        was_notifying = self._notify_started
        self._notify_started = False
        self._frames.reset()
        if self._pending is not None:
            future = self._pending[3]
            if not future.done():
                future.cancel()
            self._pending = None
        if client is None:
            return
        if client.is_connected and was_notifying:
            try:
                await client.stop_notify(CHARACTERISTIC_UUID)
            except Exception as err:
                _LOGGER.debug("Moonboon stop_notify failed for %s: %s", self.address, err)
        try:
            await client.disconnect()
        except Exception as err:
            _LOGGER.debug("Moonboon disconnect failed for %s: %s", self.address, err)

    async def _request_unlocked(self, payload: bytes, action: str) -> dict:
        client = self._client
        if client is None or not client.is_connected:
            raise MoonboonConnectionError("Moonboon BLE connection was lost")
        sequence = self._sequence
        self._sequence = sequence % 255 + 1
        command = payload[7]
        reply_op = 1 if payload[0] & 7 == 0 else 3
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending = (sequence, reply_op, command, future)
        try:
            request = with_sequence(payload, sequence)
            _LOGGER.debug("Writing Moonboon %s payload: %s", action, request.hex(" "))
            await client.write_gatt_char(CHARACTERISTIC_UUID, request, response=False)
            result = await asyncio.wait_for(future, timeout=8)
        except TimeoutError as err:
            raise MoonboonConnectionError(f"Moonboon {action} response timed out") from err
        finally:
            self._pending = None
        rc = result.get("rc", 0)
        if rc != 0 and not (rc == 6 and action in ("restart", "stop")):
            raise HomeAssistantError(f"Moonboon {action} rejected the command (rc={rc})")
        return result

    async def _read_status_unlocked(self) -> dict:
        status = await self._request_unlocked(build_read_payload(3), "check_state")
        if not isinstance(status.get("state"), str):
            raise HomeAssistantError("Moonboon did not report a motor state")
        self._apply_status(status)
        return status

    async def pair(self) -> None:
        """Bond and verify the motor answers an information request."""
        async with self._lock:
            try:
                await self.ensure_connected("pair")
                try:
                    await asyncio.wait_for(self._client.pair(), timeout=30)
                except NotImplementedError as err:
                    raise MoonboonPairingUnsupported(
                        "This Bluetooth adapter or ESPHome proxy does not support pairing"
                    ) from err
                except TimeoutError as err:
                    raise MoonboonConnectionError("Moonboon pairing timed out") from err
                except Exception as err:
                    # Some adapters bond during connect and reject a second pair().
                    _LOGGER.warning(
                        "Moonboon explicit pairing failed; checking connection: %s",
                        err,
                    )
                info = await self._request_unlocked(build_read_payload(0), "device_info")
                if not info.get("hw") and not info.get("fw"):
                    raise HomeAssistantError("Moonboon did not return device information")
            except (MoonboonPairingUnsupported, HomeAssistantError):
                raise
            except Exception as err:
                raise MoonboonConnectionError(f"Moonboon pairing failed: {err}") from err
            finally:
                await self._disconnect_unlocked()

    async def send_payloads(
        self,
        action: str,
        payloads: list[bytes],
        keep_connected: bool | None = None,
        force_reconnect: bool = False,
    ) -> None:
        async with self._lock:
            try:
                if force_reconnect:
                    await self._disconnect_unlocked()
                if action == "check_state":
                    self.last_raw_notification = None
                    self.last_decoded = None
                await self.ensure_connected(action)
                if action in ("start", "run_program"):
                    status = await self._read_status_unlocked()
                    preamble = "stop" if status["state"] == "running" else "restart"
                    await self._request_unlocked(PAYLOADS[preamble], preamble)
                    await asyncio.sleep(0.3)
                for payload in payloads:
                    command = (
                        "check_state" if payload[0] & 7 == 0
                        else "stop" if payload == PAYLOADS["stop"]
                        else action
                    )
                    result = await self._request_unlocked(payload, command)
                    if command == "check_state":
                        if not isinstance(result.get("state"), str):
                            raise HomeAssistantError("Moonboon did not report a motor state")
                        self._apply_status(result)
                if action in ("start", "run_program"):
                    await asyncio.sleep(1.5)
                    status = await self._read_status_unlocked()
                    if not self.is_running:
                        raise HomeAssistantError(
                            "Moonboon did not start; the motor needs weight in the cradle"
                        )
                elif action == "stop":
                    await asyncio.sleep(0.2)
                    await self._read_status_unlocked()
                    if self.is_running:
                        raise HomeAssistantError("Moonboon is still running after stop")
                elif action == "set_program" and self.is_running:
                    await self._read_status_unlocked()
            except HomeAssistantError:
                raise
            except Exception as err:
                raise MoonboonConnectionError(
                    f"Moonboon {action} failed ({type(err).__name__}: {err}). "
                    "Check proxy range, active connections and whether the phone app is connected."
                ) from err
            finally:
                if not keep_connected:
                    await self._disconnect_unlocked()
