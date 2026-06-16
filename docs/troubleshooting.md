# Troubleshooting

## Moonboon Is Not Discovered

Check that Home Assistant can see Bluetooth advertisements from the motor.

For ESPHome Bluetooth Proxy, make sure active connections are enabled:

```yaml
esp32_ble_tracker:

bluetooth_proxy:
  active: true
```

Move the ESP32 proxy close to the Moonboon motor. RSSI around `-78` is weak for reliable active GATT connections.

## Connection Fails During Setup

The motor must be in pairing mode.

During the add-integration flow:

1. Confirm the name and BLE address.
2. Press the physical pair button on that Moonboon motor.
3. Tick the checkbox in Home Assistant.
4. Submit.

If setup still fails:

- Move the ESPHome Bluetooth Proxy closer.
- Turn off Bluetooth on the phone temporarily.
- Close the Moonboon app.
- Retry while the motor is still in pairing mode.

## Multiple Moonboon Motors Look Similar

The setup flow shows the BLE address for each discovered motor.

Example:

```text
FC:05:80:87:08:F4
```

Use that address to identify which motor you are adding.

## Start Does Not Work After Stop

The integration sends an internal `restart` before `start` because testing showed the motor may reject a new start after recent stops.

If start still fails:

- Try again after a few seconds.
- Make sure the motor is not connected to the phone app.
- Check Home Assistant logs for BLE connection errors.

## Manual Stop Still Shows Running

The integration polls state every 30 seconds by reconnecting and reading notifications.

If manual stop or baby-movement stop still shows as running after a minute, the currently known poll payload may not return the stopped/safety state on your firmware.

Useful log lines:

```text
Moonboon notification:
Moonboon decoded notification:
Moonboon check_state result
```

Enable debug logs if needed:

```yaml
logger:
  default: warning
  logs:
    custom_components.moonboon: debug
```

If the poll only returns `{"rc": 0}` or no notification, another app status payload may need to be captured.

## Remaining Time Looks Wrong

Configured duration is in minutes.

Moonboon status notifications report remaining time in seconds. The integration displays remaining time in minutes and updates it locally once per minute.

## Old Buttons Still Exist

Earlier development versions exposed extra buttons and services such as Pair/Test Connection, Check State, and Restart.

They were removed from the user-facing integration. If old entities remain after restart, delete them from:

```text
Settings -> Devices & services -> Entities
```

## Integration Logo Does Not Show

The integration includes local assets:

```text
custom_components/moonboon/brand/icon.png
custom_components/moonboon/brand/logo.png
```

Home Assistant 2026.3+ loads local custom integration brand images from the `brand/` folder. Restart Home Assistant and clear browser cache if the logo does not update immediately.

## Phone App Cannot Connect

The phone app and Home Assistant may compete for the same BLE motor. If the app cannot connect:

- Stop current Home Assistant actions.
- Wait for the integration's BLE session to disconnect.
- Temporarily disable the Home Assistant integration if needed.
