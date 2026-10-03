"""Exercise BLE command handling offline, without Home Assistant or hardware."""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


ROOT = pathlib.Path(__file__).parent.parent / "custom_components" / "moonboon"
PACKAGE = "custom_components.moonboon"


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, protocol):
        self.protocol = protocol
        self.is_connected = True
        self.services = self
        self.running = False
        self.can_start = True
        self.pair_calls = 0
        self.pairing_unsupported = False
        self.writes = []
        self.notify = None
        self.reply = True
        self.fragment_replies = True
        self.command_rc = 0

    def get_service(self, uuid):
        return self

    def get_characteristic(self, uuid):
        return self

    async def start_notify(self, uuid, callback):
        self.notify = callback

    async def stop_notify(self, uuid):
        self.notify = None

    async def disconnect(self):
        self.is_connected = False

    async def pair(self):
        self.pair_calls += 1
        if self.pairing_unsupported:
            raise NotImplementedError
        return True

    async def write_gatt_char(self, uuid, data, response):
        self.writes.append(data)
        if data[7] == 1 and data[0] & 7 == 2:
            command = self.protocol.decode_payload(data)["command"]
            if command == "stop":
                self.running = False
            elif command == "start":
                self.running = self.can_start
        if not self.reply:
            return
        if data[7] == 3:
            body = self.protocol.encode_map(
                [
                    ("rc", self.protocol.encode_uint(0)),
                    ("state", self.protocol.encode_text("running" if self.running else "stopped")),
                    ("remaining", self.protocol.encode_uint(3600 if self.running else 0)),
                ]
            )
        elif data[7] == 0:
            body = self.protocol.encode_map([("hw", self.protocol.encode_text("Connect 2"))])
        else:
            body = self.protocol.encode_map([("rc", self.protocol.encode_uint(self.command_rc))])
        reply_op = 1 if data[0] & 7 == 0 else 3
        frame = bytes([reply_op, 0]) + len(body).to_bytes(2, "big") + data[4:8] + body
        if self.fragment_replies:
            self.notify(0, bytearray(frame[:5]))
            self.notify(0, bytearray(frame[5:]))
        else:
            self.notify(0, bytearray(frame))


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        homeassistant = types.ModuleType("homeassistant")
        components = types.ModuleType("homeassistant.components")
        bluetooth = types.ModuleType("homeassistant.components.bluetooth")
        bluetooth.async_ble_device_from_address = lambda *args, **kwargs: object()
        bluetooth.async_process_advertisements = AsyncMock(return_value=object())
        bluetooth.BluetoothScanningMode = types.SimpleNamespace(ACTIVE="active")
        bluetooth.BluetoothServiceInfoBleak = object
        components.bluetooth = bluetooth
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object
        exceptions = types.ModuleType("homeassistant.exceptions")
        exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
        const = types.ModuleType("homeassistant.const")
        const.CONF_ADDRESS = "address"
        const.CONF_NAME = "name"
        entries = types.ModuleType("homeassistant.config_entries")

        class FakeConfigFlow:
            def __init_subclass__(cls, domain=None, **kwargs):
                super().__init_subclass__(**kwargs)

            def __init__(self):
                self.hass = object()
                self.context = {}

            async def async_set_unique_id(self, unique_id):
                self.unique_id = unique_id

            def _abort_if_unique_id_configured(self):
                pass

            def async_show_form(self, **kwargs):
                return kwargs

            def async_create_entry(self, **kwargs):
                return kwargs

        entries.ConfigFlow = FakeConfigFlow
        entries.ConfigFlowResult = dict
        homeassistant.config_entries = entries
        vol = types.ModuleType("voluptuous")
        vol.Required = lambda name, **kwargs: name
        vol.Optional = lambda name, **kwargs: name
        vol.Schema = lambda data: data
        retry = types.ModuleType("bleak_retry_connector")
        retry.BleakClientWithServiceCache = object
        retry.establish_connection = AsyncMock()
        cls.modules = patch.dict(
            sys.modules,
            {
                "custom_components.moonboon": package,
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.bluetooth": bluetooth,
                "homeassistant.config_entries": entries,
                "homeassistant.const": const,
                "homeassistant.core": core,
                "homeassistant.exceptions": exceptions,
                "bleak_retry_connector": retry,
                "voluptuous": vol,
            },
        )
        cls.modules.start()
        cls.protocol = load_module("protocol")
        load_module("const")
        cls.device = load_module("device")
        cls.flow_module = load_module("config_flow")

    @classmethod
    def tearDownClass(cls):
        cls.modules.stop()
        for name in ("config_flow", "device", "const", "protocol"):
            sys.modules.pop(f"{PACKAGE}.{name}", None)

    async def asyncSetUp(self):
        self.client = FakeClient(self.protocol)
        self.device.establish_connection = self._connect
        self.motor = self.device.MoonboonDevice(object(), "AA:BB", "Moonboon")

    async def _connect(self, *args, **kwargs):
        return self.client

    async def test_pair_bonds_and_reads_info(self):
        await self.motor.pair()
        self.assertEqual(self.client.pair_calls, 1)
        self.assertEqual([raw[7] for raw in self.client.writes], [0])
        self.assertTrue(all(raw[6] != 0 for raw in self.client.writes))
        self.assertFalse(self.client.is_connected)

    async def test_unsupported_pairing_surfaces_and_disconnects(self):
        self.client.pairing_unsupported = True
        with self.assertRaises(self.device.MoonboonPairingUnsupported):
            await self.motor.pair()
        self.assertFalse(self.client.is_connected)

    async def test_start_requires_status_confirmation(self):
        self.client.can_start = False
        from custom_components.moonboon.const import PAYLOADS

        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(Exception, "needs weight"):
                await self.motor.send_payloads("start", [PAYLOADS["start"]])
        self.assertFalse(self.motor.is_running)
        self.assertEqual([raw[7] for raw in self.client.writes], [3, 1, 1, 3])

    async def test_start_and_stop_follow_real_motor_state(self):
        from custom_components.moonboon.const import PAYLOADS

        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await self.motor.send_payloads("start", [PAYLOADS["start"]])
        self.assertTrue(self.motor.is_running)
        self.assertEqual(self.motor.remaining, 3600)
        self.assertFalse(self.client.is_connected)

        self.client = FakeClient(self.protocol)
        self.client.running = True
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            await self.motor.send_payloads("stop", [PAYLOADS["stop"]])
        self.assertFalse(self.motor.is_running)
        self.assertEqual(self.motor.remaining, 0)

    async def test_nonzero_command_result_is_not_treated_as_success(self):
        from custom_components.moonboon.const import PAYLOADS

        self.client.command_rc = 3
        with patch.object(asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(Exception, "rc=3"):
                await self.motor.send_payloads("start", [PAYLOADS["start"]])
        self.assertFalse(self.client.is_connected)

    async def test_wrong_sequence_or_command_cannot_complete_request(self):
        self.client.reply = False
        from custom_components.moonboon.const import PAYLOADS

        task = asyncio.create_task(self.motor.send_payloads("check_state", [PAYLOADS["check_state"]]))
        for _ in range(10):
            await asyncio.sleep(0)
            if self.client.writes:
                break
        request = self.client.writes[0]
        body = self.protocol.encode_map([("rc", self.protocol.encode_uint(0))])
        for sequence, command in ((0, 3), (request[6], 1)):
            raw = b"\x01\x00" + len(body).to_bytes(2, "big") + b"\x00\x41" + bytes([sequence, command]) + body
            self.client.notify(0, bytearray(raw))
        self.assertFalse(task.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.client.is_connected)

    async def test_missing_response_is_connection_error_and_closes_session(self):
        from custom_components.moonboon.const import PAYLOADS

        self.client.reply = False
        with patch.object(asyncio, "wait_for", side_effect=TimeoutError):
            with self.assertRaises(self.device.MoonboonConnectionError):
                await self.motor.send_payloads("check_state", [PAYLOADS["check_state"]])
        self.assertFalse(self.client.is_connected)

    async def test_reauth_bonds_existing_entry_without_changing_its_address(self):
        flow = self.flow_module.MoonboonConfigFlow()
        flow.hass = object()
        entry = types.SimpleNamespace(data={"address": "AA:BB", "name": "Cradle"})
        flow._get_reauth_entry = lambda: entry
        flow.async_update_reload_and_abort = lambda e: {"reauthenticated": e}
        await flow.async_step_reauth(entry.data)
        result = await flow.async_step_reauth_confirm({"pair_button_pressed": True})
        self.assertIs(result["reauthenticated"], entry)
        self.assertEqual(self.client.pair_calls, 1)
        bluetooth = sys.modules["homeassistant.components.bluetooth"]
        self.assertEqual(
            bluetooth.async_process_advertisements.await_args.args[2],
            {"address": "AA:BB", "connectable": True},
        )

    async def test_pairing_flow_reports_missing_advertisement(self):
        flow = self.flow_module.MoonboonConfigFlow()
        flow.hass = object()
        flow._pending = {"address": "AA:BB", "name": "Cradle"}
        bluetooth = sys.modules["homeassistant.components.bluetooth"]
        with patch.object(bluetooth, "async_process_advertisements", side_effect=TimeoutError):
            result = await flow.async_step_pair_confirm({"pair_button_pressed": True})
        self.assertEqual(result["errors"], {"base": "not_found"})
        self.assertEqual(self.client.pair_calls, 0)


if __name__ == "__main__":
    unittest.main()
