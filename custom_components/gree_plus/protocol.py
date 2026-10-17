"""Gree+ wire protocol: encryption, message envelopes and property maps.

This module has no Home Assistant dependencies so it can be unit tested on its own.

Gree+ units talk to Gree's regional MQTT broker. The app side publishes to
``request/<mac>`` and the unit answers on ``response/<mac>``; full state snapshots
are pushed (retained) to ``status/<mac>`` and online/offline notices to
``connect/<mac>``. Every payload is a JSON envelope whose ``pack`` field is
AES-128-ECB(PKCS#7) encrypted JSON, keyed with the per-device key that the cloud
API hands out (``connect/`` notices use Gree's generic key).
"""

from __future__ import annotations

import base64
from enum import IntEnum
import json
import random
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

GENERIC_KEY = b"a3K8Bx%2r8Y7#xDh"

# Indoor/outdoor/coil temperatures are reported with a +40 offset (TemSen=60 is 20 C).
TEMP_OFFSET = 40

MIN_TEMP = 16
MAX_TEMP = 30


class Mode(IntEnum):
    """Values of the ``Mod`` property."""

    AUTO = 0
    COOL = 1
    DRY = 2
    FAN = 3
    HEAT = 4


class FanSpeed(IntEnum):
    """Values of the ``WdSpd`` property."""

    AUTO = 0
    LOW = 1
    MEDIUM_LOW = 2
    MEDIUM = 3
    MEDIUM_HIGH = 4
    HIGH = 5


# SwUpDn: 0 off, 1 full swing, 2-6 fixed (top to bottom). The protocol also defines
# 7-11 (swing within one region), but mid 10001 units answer those with 1 and the
# Gree+ app doesn't offer them, so they are left out.
VERTICAL_SWING = {
    "off": 0,
    "swing": 1,
    "fixed_top": 2,
    "fixed_upper": 3,
    "fixed_middle": 4,
    "fixed_lower": 5,
    "fixed_bottom": 6,
}

# SwingLfRig: 0 off, 1 full swing, 2-6 fixed (left to right).
HORIZONTAL_SWING = {
    "off": 0,
    "swing": 1,
    "fixed_left": 2,
    "fixed_left_center": 3,
    "fixed_center": 4,
    "fixed_right_center": 5,
    "fixed_right": 6,
}

# Properties queried on every poll. The unit returns them in a "dat" reply.
STATUS_COLS = [
    "Pow",
    "Mod",
    "SetTem",
    "TemUn",
    "WdSpd",
    "Quiet",
    "Tur",
    "SwUpDn",
    "SwingLfRig",
    "TemSen",
    "OutEnvTem",
    "InEvaTem",
    "CompressorFqy",
    "Lig",
    "Health",
    "Blo",
    "SwhSlp",
    "AllErr",
]


def encrypt_pack(payload: dict[str, Any], key: bytes) -> str:
    """Encrypt a JSON payload into a base64 ``pack`` string."""
    data = json.dumps(payload, separators=(",", ":")).encode()
    pad = 16 - len(data) % 16
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305 - protocol-mandated
    return base64.b64encode(encryptor.update(data + bytes([pad]) * pad) + encryptor.finalize()).decode()


def decrypt_pack(pack: str, key: bytes) -> dict[str, Any]:
    """Decrypt a base64 ``pack`` string into its JSON payload.

    Raises ``ValueError`` when the key is wrong or the data is malformed.
    """
    raw = base64.b64decode(pack)
    if not raw or len(raw) % 16:
        raise ValueError("pack is not a whole number of AES blocks")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305 - protocol-mandated
    plain = decryptor.update(raw) + decryptor.finalize()
    pad = plain[-1]
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad padding (wrong key?)")
    try:
        return json.loads(plain[:-pad])
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ValueError("pack did not decrypt to JSON") from err


def new_cid() -> str:
    """Return a random request id; the unit echoes it back as ``tcid``."""
    return str(random.randint(1_000_000_000, 9_999_999_999))  # noqa: S311 - not security sensitive


def build_request(mac: str, uid: int, key: bytes, payload: dict[str, Any], cid: str) -> str:
    """Build the JSON envelope published to ``request/<mac>``."""
    return json.dumps(
        {
            "cid": cid,
            "i": 0,
            "pack": encrypt_pack(payload, key),
            "t": "pack",
            "tcid": mac,
            "uid": uid,
        },
        separators=(",", ":"),
    )


def status_payload(mac: str, cols: list[str] | None = None) -> dict[str, Any]:
    """Payload asking the unit for the current value of ``cols``."""
    return {"t": "status", "mac": mac, "cols": list(cols or STATUS_COLS)}


def command_payload(mac: str, values: dict[str, int]) -> dict[str, Any]:
    """Payload setting one or more properties."""
    return {"t": "cmd", "mac": mac, "opt": list(values), "p": list(values.values())}


def state_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract ``{property: value}`` from a ``dat`` (status) or ``res`` (command ack) payload."""
    kind = payload.get("t")
    if kind == "dat":
        cols, values = payload.get("cols") or [], payload.get("dat") or []
    elif kind == "res":
        cols, values = payload.get("opt") or [], payload.get("val") or payload.get("p") or []
    else:
        return {}
    if len(cols) != len(values):
        return {}
    return dict(zip(cols, values, strict=True))


def offset_temperature(value: Any) -> float | None:
    """Convert a +40-offset temperature reading to degrees Celsius."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return value - TEMP_OFFSET
