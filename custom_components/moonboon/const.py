DOMAIN = "moonboon"
POLL_INTERVAL_SECONDS = 30

CONF_DURATION = "duration"
CONF_FADE_OUT = "fade_out"
CONF_FADE_STEPS = "fade_steps"
CONF_SPEED = "speed"

DEFAULT_ADDRESS = "FC:05:80:87:08:F4"
DEVICE_NAME = "Moonboon"

SERVICE_UUID = "8d53dc1d-1db7-4cd3-868b-8a527460aa84"
CHARACTERISTIC_UUID = "da2e7828-fbce-4e01-ae9e-261174997c48"

PAYLOADS = {
    "check_state": bytes.fromhex("0800000100410003a0"),
    "start": bytes.fromhex("0a00000f00410001a167636f6d6d616e64657374617274"),
    "stop": bytes.fromhex("0a00000e00410001a167636f6d6d616e646473746f70"),
    "restart": bytes.fromhex("0a00001100410001a167636f6d6d616e646772657374617274"),
}

PROGRAM_SERVICES = {"set_program", "run_program"}
