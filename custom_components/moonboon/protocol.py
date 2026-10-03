from __future__ import annotations

import time
from dataclasses import dataclass


COMMAND_PREFIX = bytes.fromhex("0a 00")
COMMAND_SUFFIX = bytes.fromhex("00 41 00 01")
SEQUENCE_PREFIX = bytes.fromhex("0a 00")
SEQUENCE_SUFFIX = bytes.fromhex("00 41 00 02")
MAX_SEQUENCE_PAYLOAD_LEN = 239
MAX_NOTIFICATION_BODY_LEN = 1024
SMP_GROUP = 65


@dataclass(frozen=True)
class SmpFrame:
    op: int
    command: int
    sequence: int
    payload: dict


class SmpFrameReader:
    """Collect complete SMP frames from fragmented or combined BLE notifications."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[SmpFrame]:
        self._buffer.extend(data)
        frames = []
        while len(self._buffer) >= 8:
            op = self._buffer[0] & 7
            length = int.from_bytes(self._buffer[2:4], "big")
            if (
                op > 3
                or self._buffer[0] >> 5
                or self._buffer[1] != 0
                or int.from_bytes(self._buffer[4:6], "big") != SMP_GROUP
                or length > MAX_NOTIFICATION_BODY_LEN
            ):
                del self._buffer[0]
                continue
            total = 8 + length
            if len(self._buffer) < total:
                break
            raw = bytes(self._buffer[:total])
            del self._buffer[:total]
            try:
                reader = CborReader(raw[8:])
                payload = reader.read()
                if not isinstance(payload, dict) or reader.pos != length:
                    continue
            except (TypeError, ValueError):
                continue
            frames.append(SmpFrame(op, raw[7], raw[6], payload))
        return frames


class CborReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read(self):
        return self._read_item()

    def _take(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise ValueError("truncated CBOR")
        out = self.data[self.pos : self.pos + count]
        self.pos += count
        return out

    def _peek(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("truncated CBOR")
        return self.data[self.pos]

    def _read_len(self, ai: int) -> int | None:
        if ai < 24:
            return ai
        if ai == 24:
            return self._take(1)[0]
        if ai == 25:
            return int.from_bytes(self._take(2), "big")
        if ai == 26:
            return int.from_bytes(self._take(4), "big")
        if ai == 27:
            return int.from_bytes(self._take(8), "big")
        if ai == 31:
            return None
        raise ValueError(f"unsupported CBOR additional info {ai}")

    def _read_item(self):
        initial = self._take(1)[0]
        if initial == 0xff:
            raise ValueError("unexpected CBOR break")
        major = initial >> 5
        length = self._read_len(initial & 0x1f)
        if length is None and major < 2:
            raise ValueError("indefinite length is not valid for integers")
        # ponytail: mixed-type chunks in indefinite strings still surface as
        # TypeError; harmless behind decode_payload's callers, tighten if a
        # future caller needs a strict ValueError contract.

        if major == 0:
            return length
        if major == 1:
            return -1 - length
        if major == 2:
            if length is None:
                chunks = []
                while self._peek() != 0xff:
                    chunks.append(self._read_item())
                self.pos += 1
                return b"".join(chunks)
            return self._take(length)
        if major == 3:
            if length is None:
                chunks = []
                while self._peek() != 0xff:
                    chunks.append(self._read_item())
                self.pos += 1
                return "".join(chunks)
            return self._take(length).decode()
        if major == 4:
            if length is None:
                items = []
                while self._peek() != 0xff:
                    items.append(self._read_item())
                self.pos += 1
                return items
            return [self._read_item() for _ in range(length)]
        if major == 5:
            out = {}
            if length is None:
                while self._peek() != 0xff:
                    key = self._read_item()
                    out[key] = self._read_item()
                self.pos += 1
                return out
            for _ in range(length):
                key = self._read_item()
                out[key] = self._read_item()
            return out
        if major == 7:
            if initial == 0xf4:
                return False
            if initial == 0xf5:
                return True
            if initial in (0xf6, 0xf7):
                return None
        raise ValueError(f"unsupported CBOR byte 0x{initial:02x}")


def decode_payload(data: bytes):
    if len(data) < 8:
        return None
    body_len = int.from_bytes(data[2:4], "big")
    body = data[8:]
    if body_len != len(body):
        return None
    return CborReader(body).read()


def encode_uint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative integers are not supported")
    if value < 24:
        return bytes([value])
    if value < 256:
        return bytes([0x18, value])
    if value < 65536:
        return bytes([0x19]) + value.to_bytes(2, "big")
    if value < 4294967296:
        return bytes([0x1a]) + value.to_bytes(4, "big")
    return bytes([0x1b]) + value.to_bytes(8, "big")


def encode_text(value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) < 24:
        return bytes([0x60 + len(raw)]) + raw
    if len(raw) < 256:
        return bytes([0x78, len(raw)]) + raw
    raise ValueError("text too long")


def encode_array(items: list[bytes]) -> bytes:
    if len(items) >= 24:
        raise ValueError("array too long")
    return bytes([0x80 + len(items)]) + b"".join(items)


def encode_map(items: list[tuple[str, bytes]]) -> bytes:
    if len(items) >= 24:
        raise ValueError("map too long")
    return bytes([0xa0 + len(items)]) + b"".join(
        encode_text(key) + value for key, value in items
    )


def encode_indefinite_step(speed: int, timer: int) -> bytes:
    return (
        bytes([0xbf])
        + encode_text("speed")
        + encode_uint(speed)
        + encode_text("timer")
        + encode_uint(timer)
        + bytes([0xff])
    )


def moonboon_payload(prefix: bytes, suffix: bytes, body: bytes) -> bytes:
    return prefix + len(body).to_bytes(2, "big") + suffix + body


def with_sequence(payload: bytes, sequence: int) -> bytes:
    """Set the SMP request ID; zero is reserved for unsolicited pushes."""
    if not 1 <= sequence <= 255 or len(payload) < 8:
        raise ValueError("invalid SMP request")
    return payload[:6] + bytes([sequence]) + payload[7:]


def build_read_payload(command: int) -> bytes:
    if not 0 <= command <= 255:
        raise ValueError("invalid SMP command")
    return moonboon_payload(b"\x08\x00", b"\x00\x41\x00" + bytes([command]), b"\xa0")


def build_command_payload(command: str) -> bytes:
    body = encode_map([("command", encode_text(command))])
    return moonboon_payload(COMMAND_PREFIX, COMMAND_SUFFIX, body)


def build_sequence_payload(
    speed: int,
    duration: int,
    fade_out: int = 0,
    fade_steps: int = 12,
    now_ms: int | None = None,
) -> bytes:
    speed = max(1, min(100, int(speed)))
    duration = max(1, int(duration))
    fade_out = max(0, min(int(fade_out), duration))
    fade_steps = max(1, min(12, int(fade_steps)))

    def make_steps(step_limit: int) -> list[tuple[int, int]]:
        steps: list[tuple[int, int]] = []
        hold_time = duration - fade_out
        if hold_time > 0:
            steps.append((speed, hold_time))

        if fade_out > 0:
            step_count = min(step_limit, fade_out, 12 - len(steps))
            base_timer = fade_out // step_count
            extra = fade_out % step_count
            min_speed = 8 if speed >= 8 else 1
            for index in range(step_count):
                if step_count == 1:
                    step_speed = min_speed
                else:
                    step_speed = round(
                        speed - ((speed - min_speed) * index / (step_count - 1))
                    )
                step_timer = base_timer + (1 if index < extra else 0)
                steps.append((max(min_speed, step_speed), max(1, step_timer)))
        return steps

    def make_payload(steps: list[tuple[int, int]]) -> bytes:
        sequence = encode_array(
            [
                encode_indefinite_step(step_speed, step_timer)
                for step_speed, step_timer in steps
            ]
        )
        body = encode_map(
            [
                ("sequence", sequence),
                ("time now", encode_uint(now_ms)),
            ]
        )
        return moonboon_payload(SEQUENCE_PREFIX, SEQUENCE_SUFFIX, body)

    hold_time = duration - fade_out
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    for step_limit in range(fade_steps, 0, -1):
        payload = make_payload(make_steps(step_limit))
        if len(payload) <= MAX_SEQUENCE_PAYLOAD_LEN:
            return payload

    return make_payload([(speed, hold_time or duration)])
