"""Tests for the wire protocol helpers."""

import base64
import json

import pytest

from custom_components.gree_plus.protocol import (
    GENERIC_KEY,
    build_request,
    command_payload,
    decrypt_pack,
    encrypt_pack,
    offset_temperature,
    state_from_payload,
    status_payload,
)

KEY = b"0123456789abcdef"
MAC = "aabbccddeeff"


def test_pack_round_trip() -> None:
    payload = {"t": "cmd", "mac": MAC, "opt": ["SetTem"], "p": [19]}
    pack = encrypt_pack(payload, KEY)
    assert len(base64.b64decode(pack)) % 16 == 0
    assert decrypt_pack(pack, KEY) == payload


def test_pack_is_compact_json() -> None:
    """The unit expects compact JSON (what the app sends), padded with PKCS#7."""
    pack = encrypt_pack({"a": 1}, KEY)
    # '{"a":1}' is 7 bytes -> one block with 9 bytes of padding
    assert len(base64.b64decode(pack)) == 16


def test_wrong_key_is_rejected() -> None:
    pack = encrypt_pack({"t": "status"}, KEY)
    with pytest.raises(ValueError):
        decrypt_pack(pack, GENERIC_KEY)


def test_truncated_pack_is_rejected() -> None:
    with pytest.raises(ValueError):
        decrypt_pack(base64.b64encode(b"short").decode(), KEY)


def test_build_request_envelope() -> None:
    raw = build_request(MAC, 1234567, KEY, status_payload(MAC, ["Pow"]), "1111111111")
    envelope = json.loads(raw)
    assert envelope["t"] == "pack"
    assert envelope["tcid"] == MAC
    assert envelope["cid"] == "1111111111"
    assert envelope["uid"] == 1234567
    assert envelope["i"] == 0
    assert decrypt_pack(envelope["pack"], KEY) == {"t": "status", "mac": MAC, "cols": ["Pow"]}


def test_command_payload_keeps_order() -> None:
    assert command_payload(MAC, {"Mod": 1, "Pow": 1}) == {
        "t": "cmd",
        "mac": MAC,
        "opt": ["Mod", "Pow"],
        "p": [1, 1],
    }


def test_state_from_status_reply() -> None:
    reply = {
        "t": "dat",
        "mac": MAC,
        "r": 200,
        "cols": ["Pow", "Mod", "SetTem", "WdSpd"],
        "dat": [1, 0, 19, 0],
    }
    assert state_from_payload(reply) == {"Pow": 1, "Mod": 0, "SetTem": 19, "WdSpd": 0}


def test_state_from_command_ack() -> None:
    ack = {"t": "res", "mac": MAC, "opt": ["SetTem", "TemUn"], "p": [19, 0], "r": 200}
    assert state_from_payload(ack) == {"SetTem": 19, "TemUn": 0}


def test_state_from_ack_prefers_val() -> None:
    ack = {"t": "res", "opt": ["SetTem"], "p": [35], "val": [30], "r": 200}
    assert state_from_payload(ack) == {"SetTem": 30}


@pytest.mark.parametrize(
    "payload",
    [
        {"t": "connect", "online": 1},
        {"t": "dat", "cols": ["Pow", "Mod"], "dat": [1]},
        {"t": "dat"},
    ],
)
def test_state_from_other_payloads(payload: dict) -> None:
    assert state_from_payload(payload) == {}


@pytest.mark.parametrize(("raw", "expected"), [(60, 20), (50, 10), (40, 0), (0, None), (None, None), ("x", None)])
def test_offset_temperature(raw, expected) -> None:
    assert offset_temperature(raw) == expected
