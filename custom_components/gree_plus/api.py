"""Gree+ cloud REST API: account login and device (key) discovery."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

APP_ID = "4920681951525131286"
APP_HASH = "0fa513124aa97781d1f3f40d61ca1a89"
API_KEY = b"#G$&^jgfujy6ujxt"

# Region -> (REST API base URL, MQTT broker host). The Gree+ app's country picker
# maps onto these servers; New Zealand accounts live on the Australian one.
REGIONS: dict[str, tuple[str, str]] = {
    "australia": ("https://augrih.gree.com", "mqtt-au.gree.com"),
    "europe": ("https://eugrih.gree.com", "mqtt-eu.gree.com"),
    "north_america": ("https://nagrih.gree.com", "mqtt-na.gree.com"),
    "south_america": ("https://sagrih.gree.com", "mqtt-sa.gree.com"),
    "latin_america": ("https://lagrih.gree.com", "mqtt-la.gree.com"),
    "east_south_asia": ("https://hkgrih.gree.com", "mqtt-as.gree.com"),
    "india": ("https://ingrih.gree.com", "mqtt-in.gree.com"),
    "middle_east": ("https://megrih.gree.com", "mqtt-me.gree.com"),
    "russia": ("https://rugrih.gree.com", "mqtt-ru.gree.com"),
    "china": ("https://grih.gree.com", "mqtt.gree.com"),
}
MQTT_PORT = 1984

# Response code for "wrong password or unknown user" on this server.
_BAD_CREDENTIALS = 411


class GreeApiError(Exception):
    """The Gree cloud API returned an error or could not be reached."""


class GreeAuthError(GreeApiError):
    """The account credentials were rejected."""


@dataclass(frozen=True, slots=True)
class CloudDevice:
    """A unit registered to the account."""

    mac: str
    name: str
    key: str
    model_id: str
    firmware: str
    room: str


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()  # noqa: S324 - protocol-mandated


def _encrypt(body: str) -> str:
    data = body.encode()
    pad = 16 - len(data) % 16
    encryptor = Cipher(algorithms.AES(API_KEY), modes.ECB()).encryptor()  # noqa: S305
    return base64.b64encode(encryptor.update(data + bytes([pad]) * pad) + encryptor.finalize()).decode()


def _decrypt(body: str) -> dict[str, Any]:
    decryptor = Cipher(algorithms.AES(API_KEY), modes.ECB()).decryptor()  # noqa: S305
    plain = decryptor.update(base64.b64decode(body)) + decryptor.finalize()
    return json.loads(plain[: -plain[-1]])


class GreeCloudApi:
    """Minimal client for the endpoints the integration needs."""

    def __init__(self, session: aiohttp.ClientSession, region: str, username: str, password: str) -> None:
        self._session = session
        self._base_url = REGIONS[region][0]
        self._username = username
        self._password = password
        self.uid: int | None = None
        self.token: str | None = None

    async def _post(self, endpoint: str, payload: dict[str, Any], signed: list[str]) -> dict[str, Any]:
        now = datetime.now(UTC)
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        ts = int(now.timestamp())
        body = {
            "api": {
                "appId": APP_ID,
                "r": ts,
                "t": stamp,
                "vc": _md5(f"{APP_ID}_{APP_HASH}_{stamp}_{ts}"),
            },
            "datVc": _md5(f"{APP_HASH}_" + "_".join(str(payload[k]) for k in signed)),
            **payload,
        }
        try:
            async with self._session.post(
                self._base_url + endpoint,
                data=_encrypt(json.dumps(body)),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Gaen1": "5ac2bdf935bcca70",
                    "Charset": "utf-8",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                resp.raise_for_status()
                raw = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise GreeApiError(f"{endpoint}: {err}") from err

        result = _decrypt(raw["enRes"]) if "enRes" in raw else raw
        code = result.get("r")
        if code == _BAD_CREDENTIALS:
            raise GreeAuthError(result.get("msg", "invalid credentials"))
        if code != 200:
            raise GreeApiError(f"{endpoint}: r={code} {result.get('msg', '')}".strip())
        return result

    async def login(self) -> int:
        """Log in and return the account's user id."""
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        psw = _md5(_md5(_md5(self._password) + self._password) + stamp)
        result = await self._post(
            "/App/UserLoginV2",
            {"psw": psw, "t": stamp, "user": self._username},
            ["user", "psw", "t"],
        )
        self.uid = int(result["uid"])
        self.token = str(result["token"])
        return self.uid

    async def get_devices(self) -> list[CloudDevice]:
        """Return every unit in every home on the account."""
        if self.uid is None:
            await self.login()
        homes = await self._post("/App/GetHomes", {"token": self.token, "uid": self.uid}, ["token", "uid"])
        devices: dict[str, CloudDevice] = {}
        for home in homes.get("home", []):
            rooms = await self._post(
                "/App/GetDevsInRoomsOfHomeV2",
                {"token": self.token, "homeId": home["id"], "uid": self.uid},
                ["token", "uid", "homeId"],
            )
            for room in rooms.get("rooms", []):
                for dev in room.get("devs", []):
                    mac = str(dev.get("mac", "")).strip().lower()
                    key = str(dev.get("key", "")).strip()
                    if not mac or len(key) != 16:
                        continue
                    devices[mac] = CloudDevice(
                        mac=mac,
                        name=str(dev.get("devNoteName") or dev.get("name") or mac).strip(),
                        key=key,
                        model_id=str(dev.get("mid", "")),
                        firmware=str(dev.get("ver", "")),
                        room=str(room.get("roomName", "")).strip(),
                    )
        return list(devices.values())
