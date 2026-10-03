# Moonboon Home Assistant Integration

Custom Home Assistant integration for controlling Moonboon BLE motors through Home Assistant Bluetooth, including ESPHome Bluetooth Proxy.

This is an unofficial reverse-engineered integration.

## Features

- Bluetooth discovery for connectable devices with names starting `Moonboon`.
- Setup that bonds with the motor and reads device information; re-pairing when repeated connections fail.
- Main on/off switch.
- Speed control, `1` to `100`.
- Duration control in minutes, `1` to `720` / 12 hours.
- Fade-out switch.
- Remaining-time sensor in minutes.
- Automatic state polling every 30 seconds.
- Local remaining-time countdown once per minute.
- Supports ESPHome Bluetooth Proxy.
- Matches BLE replies to requests and handles notifications split across packets.

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

### HACS

HACS is the recommended installation method.

1. Open HACS in Home Assistant.
2. Go to Integrations.
3. Open the three-dot menu and select Custom repositories.
4. Add this repository:

```text
https://github.com/Scholdan/moonboon
```

5. Select category `Integration`.
6. Install Moonboon.
7. Restart Home Assistant.
8. Go to Settings -> Devices & services and add Moonboon.

If you previously installed this integration manually, remove the old folder before installing through HACS:

```text
/config/custom_components/moonboon/
```

### Manual

Copy `custom_components/moonboon/` from this repository into Home Assistant:

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
7. Home Assistant waits for the motor to advertise, bonds, and reads its device information before creating the device.

Keep the motor in pairing mode until setup finishes. An ESPHome Bluetooth Proxy must support pairing; if yours does not, update its firmware or use a local Bluetooth adapter. After three consecutive connection failures while the motor is still visible, Home Assistant offers a re-pair flow. A full Home Assistant restart is required after upgrading this integration.

If you have multiple Moonboon motors, use the BLE address shown in the setup flow to identify the correct one.

## Entities

The integration creates a Home Assistant device with these entities:

- Main switch: starts and stops the motor.
- Fade Out switch: enables fade-out across the selected duration.
- Speed number: `1` to `100`.
- Duration number: minutes, `1` to `720`.
- Remaining sensor: minutes remaining.

## Behavior

Starting reads the current state, sends a stop if already running or a restart otherwise, then applies the program and starts. The integration checks command replies and reads the motor state again before reporting success. If the motor refuses to run (for example, when the cradle is empty), Home Assistant shows an error instead of claiming it started.

Fade-out still spans the full selected duration; upgrading does not change the existing fade-out switch or `fade_steps` service parameter.

The integration uses short BLE sessions:

- Connect.
- Subscribe briefly to notifications.
- Write command or poll state.
- Reassemble notifications and match each reply to its command.
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
