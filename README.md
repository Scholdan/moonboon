# Moonboon Home Assistant Integration

Custom Home Assistant integration for controlling Moonboon BLE motors through Home Assistant Bluetooth, including ESPHome Bluetooth Proxy.

This is an unofficial reverse-engineered integration.

## Features

- Bluetooth discovery for connectable devices named `Moonboon`.
- Pairing-aware setup flow with instructions to press the physical motor pair button.
- Main on/off switch.
- Speed control, `1` to `100`.
- Duration control in minutes, `1` to `720` / 12 hours.
- Fade-out switch.
- Remaining-time sensor in minutes.
- Automatic state polling every 30 seconds.
- Local remaining-time countdown once per minute.
- Supports ESPHome Bluetooth Proxy.

## Requirements

- Home Assistant with Bluetooth support.
- A BLE adapter or ESPHome Bluetooth Proxy near the Moonboon motor.
- ESPHome proxy must support active connections:

```yaml
esp32_ble_tracker:

bluetooth_proxy:
  active: true
```

## Installation

Copy the integration folder into Home Assistant:

```text
/config/custom_components/moonboon/
```

The final structure should include:

```text
/config/custom_components/moonboon/manifest.json
/config/custom_components/moonboon/__init__.py
/config/custom_components/moonboon/config_flow.py
```

Restart Home Assistant.

## Setup

1. Make sure the ESPHome Bluetooth Proxy can see the motor.
2. Go to Settings -> Devices & services.
3. Add the discovered `Moonboon` integration.
4. Confirm the BLE address and name.
5. On the pairing step, press the physical pair button on the Moonboon motor.
6. Tick the checkbox and submit.
7. Home Assistant verifies the BLE connection before creating the device.

If you have multiple Moonboon motors, use the BLE address shown in the setup flow to identify the correct one.

## Entities

The integration creates a Home Assistant device with these entities:

- Main switch: starts and stops the motor.
- Fade Out switch: enables fade-out across the selected duration.
- Speed number: `1` to `100`.
- Duration number: minutes, `1` to `720`.
- Remaining sensor: minutes remaining.

## Behavior

Starting sends an internal restart before applying the program and start command. This matches observed behavior where the motor may reject a new start after a recent stop unless reset first.

The integration uses short BLE sessions:

- Connect.
- Subscribe briefly to notifications.
- Write command or poll state.
- Read any notifications.
- Disconnect.

State polling runs every 30 seconds. Remaining time is also counted down locally once per minute.

## Services

The integration keeps a small service surface for automation use:

```yaml
service: moonboon.start
data: {}
```

```yaml
service: moonboon.stop
data: {}
```

```yaml
service: moonboon.run_program
data:
  speed: 50
  duration: 60
  fade_out: true
```

```yaml
service: moonboon.set_program
data:
  speed: 50
  duration: 60
  fade_out: false
```

## Documentation

- Reverse engineering notes: `docs/reverse-engineering.md`
- Troubleshooting: `docs/troubleshooting.md`

## Limitations

- Pairing requires pressing the physical Moonboon pair button.
- Manual stop or baby-movement stop depends on what the motor reports during the next state poll.
- The phone app may compete with Home Assistant for BLE access.
- The protocol is reverse engineered and may change with firmware/app updates.

## Disclaimer

This project is not affiliated with Moonboon. Use at your own risk, especially around baby sleep equipment.
