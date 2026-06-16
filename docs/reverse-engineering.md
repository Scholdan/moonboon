# Moonboon BLE Reverse Engineering

This document summarizes the BLE protocol details discovered while building the Home Assistant integration.

## Transport

Moonboon control uses Bluetooth Low Energy GATT.

The Android app is a Flutter app and uses FlutterBluePlus for normal BLE communication. Nordic BLE / mcumgr libraries are also bundled, but those appear related to firmware update paths rather than normal motor control.

## Discovery

Observed advertisement:

```json
{
  "name": "Moonboon",
  "address": "FC:05:80:87:08:F4",
  "connectable": true,
  "raw": "02010609094d6f6f6e626f6f6e"
}
```

The app strings also include the likely device prefix `MB-BM-01-`.

## GATT

Confirmed normal-control service:

```text
8d53dc1d-1db7-4cd3-868b-8a527460aa84
```

Confirmed normal-control characteristic:

```text
da2e7828-fbce-4e01-ae9e-261174997c48
```

Observed Android GATT cache handles:

```text
service:                  0x0010..0x0013
characteristic decl:       0x0011
characteristic value:      0x0012
cccd:                      0x0013
```

Characteristic properties:

```text
0x14 = write without response + notify
```

Writes and notifications both use the same characteristic.

## Pairing

The motor is pairing-aware. Home Assistant / ESPHome Bluetooth Proxy failed to establish a connection until the physical Moonboon pair button was pressed.

The integration setup flow therefore asks the user to press the pair button before it verifies the BLE connection and creates the device.

## Payload Format

Captured characteristic values use an 8-byte Moonboon header followed by CBOR.

Header fields observed:

```text
byte 0:     kind
bytes 2-3: big-endian CBOR body length
bytes 4-5: group, observed 0x0041
byte 7:     channel
bytes 8..:  CBOR body
```

Command writes use:

```text
kind:    0x0a
group:   0x0041
channel: 0x01
body:    {"command": "..."}
```

Sequence writes use:

```text
kind:    0x0a
group:   0x0041
channel: 0x02
body:    {"sequence": [...], "time now": <epoch_ms>}
```

State poll observed:

```text
08 00 00 01 00 41 00 03 a0
```

## Commands

Start:

```text
hex:    0a 00 00 0f 00 41 00 01 a1 67 63 6f 6d 6d 61 6e 64 65 73 74 61 72 74
base64: CgAADwBBAAGhZ2NvbW1hbmRlc3RhcnQ=
cbor:   {"command": "start"}
```

Stop:

```text
hex:    0a 00 00 0e 00 41 00 01 a1 67 63 6f 6d 6d 61 6e 64 64 73 74 6f 70
base64: CgAADgBBAAGhZ2NvbW1hbmRkc3RvcA==
cbor:   {"command": "stop"}
```

Restart:

```text
hex:    0a 00 00 11 00 41 00 01 a1 67 63 6f 6d 6d 61 6e 64 67 72 65 73 74 61 72 74
base64: CgAAEQBBAAGhZ2NvbW1hbmRncmVzdGFydA==
cbor:   {"command": "restart"}
```

The Home Assistant integration sends `restart` before `start` because repeated start/stop testing showed the motor may otherwise reject a new start after recent stops.

## Program / Sequence

Program writes are CBOR maps with a `sequence` array and `time now` timestamp.

Each sequence item contains:

```text
speed: 1..100
timer: minutes
```

Example decoded short sequence:

```python
{
  "sequence": [
    {"speed": 50, "timer": 115},
    {"speed": 41, "timer": 1},
    {"speed": 33, "timer": 1},
    {"speed": 25, "timer": 1},
    {"speed": 16, "timer": 1},
    {"speed": 8, "timer": 1}
  ],
  "time now": 1780653191728
}
```

Example decoded long fade-out sequence:

```python
{
  "sequence": [
    {"speed": 97, "timer": 20},
    {"speed": 88, "timer": 9},
    {"speed": 80, "timer": 9},
    {"speed": 72, "timer": 9},
    {"speed": 64, "timer": 9},
    {"speed": 56, "timer": 9},
    {"speed": 48, "timer": 9},
    {"speed": 40, "timer": 9},
    {"speed": 32, "timer": 9},
    {"speed": 24, "timer": 9},
    {"speed": 16, "timer": 9},
    {"speed": 8, "timer": 9}
  ],
  "time now": 1780653195815
}
```

The app maximum duration appears to be 12 hours, represented as `720` minutes.

## Notifications

Observed ACK notification:

```text
hex:  03 00 00 06 00 41 00 01 bf 62 72 63 00 ff
cbor: {"rc": 0}
```

Observed running-state notification:

```python
{
  "rc": 0,
  "state": "running",
  "duration": 0,
  "remaining": 6899,
  "remaining total": 7199,
  "current sequence": 1,
  "total sequences": 6
}
```

Status notifications report remaining time in seconds, while configured program timers are in minutes.

## Capture Method

Passive nRF52840 sniffing captured advertisements but not encrypted GATT payloads.

Android HCI snoop files in bugreports were empty 16-byte stubs on the tested phone.

The successful capture path was a patched Android APK with smali logging at the FlutterBluePlus boundary:

```text
adb logcat -s MoonboonHook:W
```

The logger emitted raw characteristic values as Base64 for writes and notifications.

## Unknowns

- Whether there are additional status poll commands used by the app after manual stops or movement stops.
- Exact notification values for all error/safety states.
- Whether all firmware versions use the same service, characteristic, and payload format.
- Whether the motor supports more than one bonded/connected central cleanly.
