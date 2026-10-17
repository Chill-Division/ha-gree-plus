"""Fixtures: a fake Gree cloud account and a fake broker that behaves like the real units."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gree_plus.api import CloudDevice, GreeAuthError
from custom_components.gree_plus.cloud_mqtt import GreeRequestError
from custom_components.gree_plus.const import CONF_REGION, DOMAIN

MAC = "aabbccddeeff"
UID = 1234567
USERNAME = "grower@example.com"
PASSWORD = "hunter2"

DEVICE = CloudDevice(
    mac=MAC, name="Mezz AC", key="0123456789abcdef", model_id="10001", firmware="V3.2.M", room="Nursery"
)

# What a unit at the nursery baseline reports.
BASELINE_STATE = {
    "Pow": 1,
    "Mod": 0,
    "SetTem": 19,
    "TemUn": 0,
    "WdSpd": 0,
    "Quiet": 0,
    "Tur": 0,
    "SwUpDn": 0,
    "SwingLfRig": 0,
    "TemSen": 60,
    "OutEnvTem": 58,
    "InEvaTem": 61,
    "CompressorFqy": 0,
    "Lig": 1,
    "Health": 1,
    "Blo": 0,
    "SwhSlp": 0,
    "AllErr": 0,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load integrations from custom_components."""


class FakeApi:
    """Stands in for GreeCloudApi. ``servers`` maps region -> whether the account lives there."""

    devices: list[CloudDevice] = [DEVICE]
    home_region = "australia"
    fail_with: Exception | None = None
    get_devices_calls = 0

    def __init__(self, session: Any, region: str, username: str, password: str) -> None:
        self.region = region
        self.password = password
        self.uid: int | None = None
        self.token: str | None = None

    async def login(self) -> int:
        if FakeApi.fail_with is not None:
            raise FakeApi.fail_with
        if self.region != FakeApi.home_region or self.password != PASSWORD:
            raise GreeAuthError("The password is incorrect or the user does not exist.")
        self.uid, self.token = UID, "token"
        return UID

    async def get_devices(self) -> list[CloudDevice]:
        FakeApi.get_devices_calls += 1
        return list(FakeApi.devices)


class FakeMqttClient:
    """Stands in for GreeMqttClient; answers like a real unit."""

    instance: FakeMqttClient | None = None

    def __init__(self, loop, host, port, uid, token, ssl_context, refresh_token) -> None:
        self.host = host
        self.handlers: dict[str, Any] = {}
        self.keys: dict[str, str] = {}
        self.state: dict[str, dict[str, Any]] = {}
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.on_bad_key = None
        self.unreachable = False
        self.connected = False
        FakeMqttClient.instance = self

    def register(self, mac: str, key: str, handler) -> None:
        self.handlers[mac] = handler
        self.keys[mac] = key
        self.state[mac] = dict(BASELINE_STATE)

    def set_key(self, mac: str, key: str) -> None:
        self.keys[mac] = key

    async def start(self) -> None:
        self.connected = True

    async def stop(self) -> None:
        self.connected = False

    async def request(self, mac: str, payload: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
        self.sent.append((mac, payload))
        if self.unreachable:
            raise GreeRequestError("no reply")
        state = self.state[mac]
        if payload["t"] == "status":
            cols = payload["cols"]
            return {"t": "dat", "mac": mac, "r": 200, "cols": cols, "dat": [state.get(c, 0) for c in cols]}
        state.update(zip(payload["opt"], payload["p"], strict=True))
        return {"t": "res", "mac": mac, "r": 200, "opt": payload["opt"], "p": payload["p"]}

    def push(self, mac: str, kind: str, payload: dict[str, Any], retained: bool = False) -> None:
        self.handlers[mac](kind, payload, retained)

    def commands(self) -> list[dict[str, Any]]:
        return [dict(zip(p["opt"], p["p"], strict=True)) for _, p in self.sent if p["t"] == "cmd"]


@pytest.fixture
def fake_cloud() -> Generator[type[FakeApi]]:
    """Patch the cloud API everywhere it's used."""
    FakeApi.devices = [DEVICE]
    FakeApi.home_region = "australia"
    FakeApi.fail_with = None
    FakeApi.get_devices_calls = 0
    with (
        patch("custom_components.gree_plus.GreeCloudApi", FakeApi),
        patch("custom_components.gree_plus.config_flow.GreeCloudApi", FakeApi),
    ):
        yield FakeApi


@pytest.fixture
def fake_broker(fake_cloud: type[FakeApi]) -> Generator[type[FakeMqttClient]]:
    """Patch the broker client."""
    FakeMqttClient.instance = None
    with patch("custom_components.gree_plus.GreeMqttClient", FakeMqttClient):
        yield FakeMqttClient


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured Gree+ account."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(UID),
        title=USERNAME,
        data={"username": USERNAME, "password": PASSWORD, CONF_REGION: "australia"},
    )
