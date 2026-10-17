"""Tests for the Gree+ REST client against mocked (encrypted) server responses."""

import base64
import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.gree_plus.api import (
    API_KEY,
    GreeApiError,
    GreeAuthError,
    GreeCloudApi,
    _decrypt,
    _encrypt,
)

BASE = "https://augrih.gree.com"


def _enc_response(payload: dict) -> dict:
    return {"enRes": _encrypt(json.dumps(payload))}


def _request_body(mock: AiohttpClientMocker, index: int) -> dict:
    """Decrypt the body the client posted."""
    return _decrypt(mock.mock_calls[index][2])


def test_api_cipher_round_trip() -> None:
    assert len(API_KEY) == 16
    assert _decrypt(_encrypt('{"a": 1}')) == {"a": 1}
    assert len(base64.b64decode(_encrypt("x" * 16))) == 32  # full block of padding


async def test_login_and_devices(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{BASE}/App/UserLoginV2", json=_enc_response({"r": 200, "uid": 42, "token": "tok"}))
    aioclient_mock.post(f"{BASE}/App/GetHomes", json=_enc_response({"r": 200, "home": [{"id": 7, "name": "Home"}]}))
    aioclient_mock.post(
        f"{BASE}/App/GetDevsInRoomsOfHomeV2",
        json=_enc_response(
            {
                "r": 200,
                "rooms": [
                    {
                        "roomName": "U11",
                        "devs": [
                            {
                                "mac": "AABBCCDDEEFF",
                                "name": "AC1",
                                "key": "0123456789abcdef",
                                "mid": "10001",
                                "ver": "V3.2.M",
                            },
                            {"mac": "aabbccddee00", "name": "Broken", "key": ""},
                        ],
                    },
                    {
                        "roomName": "Office",
                        "devs": [
                            {
                                "mac": "112233445566",
                                "name": "AC2",
                                "devNoteName": "Office AC",
                                "key": "fedcba9876543210",
                            }
                        ],
                    },
                ],
            }
        ),
    )
    api = GreeCloudApi(async_get_clientsession(hass), "australia", "me@example.com", "pw")
    devices = await api.get_devices()

    assert api.uid == 42 and api.token == "tok"
    assert [(d.mac, d.name, d.room, d.model_id) for d in devices] == [
        ("aabbccddeeff", "AC1", "U11", "10001"),
        ("112233445566", "Office AC", "Office", ""),
    ]

    login = _request_body(aioclient_mock, 0)
    assert login["user"] == "me@example.com"
    assert login["api"]["appId"] == "4920681951525131286"
    assert len(login["psw"]) == 32 and login["psw"] != "pw"
    assert _request_body(aioclient_mock, 2)["homeId"] == 7


async def test_bad_credentials(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        f"{BASE}/App/UserLoginV2",
        json=_enc_response({"r": 411, "msg": "The password is incorrect or the user does not exist."}),
    )
    api = GreeCloudApi(async_get_clientsession(hass), "australia", "me@example.com", "bad")
    with pytest.raises(GreeAuthError):
        await api.login()


@pytest.mark.parametrize(
    "response",
    [
        {"status": 500},
        {"json": _enc_response({"r": 500, "msg": "server error"})},
        {"text": "not json"},
    ],
)
async def test_api_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, response: dict) -> None:
    aioclient_mock.post(f"{BASE}/App/UserLoginV2", **response)
    api = GreeCloudApi(async_get_clientsession(hass), "australia", "me@example.com", "pw")
    with pytest.raises(GreeApiError):
        await api.login()
