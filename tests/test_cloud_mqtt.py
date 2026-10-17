"""Tests for broker message routing (without a real broker)."""

import asyncio
import json
import ssl
from types import SimpleNamespace
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt
import pytest

from custom_components.gree_plus.cloud_mqtt import GreeMqttClient, GreeRequestError
from custom_components.gree_plus.protocol import GENERIC_KEY, decrypt_pack, encrypt_pack

MAC = "aabbccddeeff"
KEY = "0123456789abcdef"


def _envelope(payload: dict, key: bytes, **extra) -> bytes:
    return json.dumps({"t": "pack", "i": 0, "uid": 0, "cid": MAC, "pack": encrypt_pack(payload, key), **extra}).encode()


@pytest.fixture
async def client():
    async def refresh() -> str:
        return "new-token"

    cli = GreeMqttClient(
        asyncio.get_running_loop(), "mqtt-au.gree.com", 1984, 42, "tok", ssl.create_default_context(), refresh
    )
    received: list = []
    cli.register(MAC, KEY, lambda kind, payload, retained: received.append((kind, payload, retained)))
    cli.received = received
    cli._client = MagicMock()
    cli._client.publish.return_value = SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)
    return cli


async def test_request_matches_reply_by_cid(client: GreeMqttClient) -> None:
    client._connected.set()
    task = asyncio.create_task(client.request(MAC, {"t": "status", "mac": MAC, "cols": ["Pow"]}, timeout=2))
    await asyncio.sleep(0)

    topic, raw = client._client.publish.call_args.args[:2]
    envelope = json.loads(raw)
    assert topic == f"request/{MAC}"
    assert envelope["tcid"] == MAC and envelope["uid"] == 42
    assert decrypt_pack(envelope["pack"], KEY.encode()) == {"t": "status", "mac": MAC, "cols": ["Pow"]}

    # Someone else's reply on the same topic is passed to the handler, not to us.
    other = {"t": "dat", "cols": ["Pow"], "dat": [0]}
    client._dispatch(f"response/{MAC}", _envelope(other, KEY.encode(), tcid="999"), False)
    reply = {"t": "dat", "cols": ["Pow"], "dat": [1], "r": 200}
    client._dispatch(f"response/{MAC}", _envelope(reply, KEY.encode(), tcid=envelope["cid"]), False)

    assert await task == reply
    assert client.received == [("response", other, False)]


async def test_request_requires_connection(client: GreeMqttClient) -> None:
    with pytest.raises(GreeRequestError):
        await client.request(MAC, {"t": "status"})


async def test_request_timeout(client: GreeMqttClient) -> None:
    client._connected.set()
    with pytest.raises(GreeRequestError, match="no reply"):
        await client.request(MAC, {"t": "status"}, timeout=0.01)
    assert not client._pending


async def test_publish_failure(client: GreeMqttClient) -> None:
    client._connected.set()
    client._client.publish.return_value = SimpleNamespace(rc=mqtt.MQTT_ERR_NO_CONN)
    with pytest.raises(GreeRequestError, match="publish failed"):
        await client.request(MAC, {"t": "status"})


async def test_status_and_connect_routing(client: GreeMqttClient) -> None:
    status = {"t": "dat", "cols": ["SetTem"], "dat": [19]}
    client._dispatch(f"status/{MAC}", _envelope(status, KEY.encode()), True)
    online = {"t": "connect", "online": 1, "mac": MAC}
    client._dispatch(f"connect/{MAC}", _envelope(online, GENERIC_KEY), True)
    client._dispatch("status/112233445566", _envelope(status, KEY.encode()), False)  # not ours
    assert client.received == [("status", status, True), ("connect", online, True)]


async def test_bad_key_reported_for_live_messages_only(client: GreeMqttClient) -> None:
    bad: list[str] = []
    client.on_bad_key = bad.append
    wrong = b"fedcba9876543210"
    client._dispatch(f"status/{MAC}", _envelope({"t": "dat"}, wrong), True)  # stale retained
    client._dispatch(f"status/{MAC}", b"not json", False)
    client._dispatch(f"status/{MAC}", _envelope({"t": "dat"}, wrong), False)
    assert bad == [MAC, MAC]

    client.set_key(MAC, "fedcba9876543210")
    client._dispatch(f"status/{MAC}", _envelope({"t": "dat", "cols": [], "dat": []}, wrong), False)
    assert client.received[-1][0] == "status"


async def test_connect_callbacks_subscribe_and_refresh_token(client: GreeMqttClient) -> None:
    ok = SimpleNamespace(is_failure=False)
    client._on_connect(client._client, None, None, ok, None)
    await asyncio.sleep(0)
    assert client.connected
    topics = [t for t, _ in client._client.subscribe.call_args.args[0]]
    assert topics == [f"response/{MAC}/#", f"status/{MAC}/#", f"connect/{MAC}"]

    client._on_disconnect(client._client, None, None, "gone", None)
    await asyncio.sleep(0)
    assert not client.connected

    class Refused:
        is_failure = True

        def __str__(self) -> str:
            return "Not authorized"

    client._on_connect(client._client, None, None, Refused(), None)
    await asyncio.sleep(0.05)
    client._client.username_pw_set.assert_called_with("42", "new-token")
