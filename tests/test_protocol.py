"""Self-check for the Moonboon CBOR codec. Run with pytest or plain python."""
import importlib.util
import pathlib

def _load(name):
    spec = importlib.util.spec_from_file_location(
        name,
        pathlib.Path(__file__).parent.parent / f"custom_components/moonboon/{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


protocol = _load("protocol")
const = _load("const")


def test_decode_captured_payloads():
    assert protocol.decode_payload(const.PAYLOADS["start"]) == {"command": "start"}
    assert protocol.decode_payload(const.PAYLOADS["stop"]) == {"command": "stop"}
    assert protocol.decode_payload(const.PAYLOADS["restart"]) == {"command": "restart"}


def test_ack_notification():
    ack = bytes.fromhex("03 00 00 06 00 41 00 01 bf 62 72 63 00 ff".replace(" ", ""))
    assert protocol.decode_payload(ack) == {"rc": 0}


def test_command_payload_matches_capture():
    for command in ("start", "stop", "restart"):
        assert protocol.build_command_payload(command) == const.PAYLOADS[command]


def test_sequence_round_trip():
    payload = protocol.build_sequence_payload(97, 119, 119, now_ms=1780653195815)
    assert len(payload) <= protocol.MAX_SEQUENCE_PAYLOAD_LEN
    decoded = protocol.decode_payload(payload)
    assert decoded["time now"] == 1780653195815
    steps = decoded["sequence"]
    assert len(steps) == 12
    assert steps[0]["speed"] == 97
    assert steps[-1]["speed"] == 8
    assert sum(step["timer"] for step in steps) == 119


def test_no_fade_is_single_step():
    decoded = protocol.decode_payload(
        protocol.build_sequence_payload(50, 60, 0, now_ms=0)
    )
    assert decoded["sequence"] == [{"speed": 50, "timer": 60}]


def test_malformed_cbor_raises_value_error():
    malformed = [
        "bf627263",  # indefinite map cut off before the 0xff break
        "1f",  # indefinite length on an unsigned int
        "3f",  # indefinite length on a negative int
    ]
    for body in malformed:
        try:
            protocol.CborReader(bytes.fromhex(body)).read()
        except ValueError:
            continue
        raise AssertionError(f"CBOR body {body} did not raise ValueError")


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            func()
    print("all protocol checks passed")
