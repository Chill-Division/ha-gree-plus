"""Tests for the Gree+ config flow."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gree_plus.api import GreeApiError
from custom_components.gree_plus.const import CONF_REGION, DOMAIN

from .conftest import PASSWORD, UID, USERNAME


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_auto_region_finds_account(hass: HomeAssistant, fake_cloud, fake_broker) -> None:
    """Auto-detect tries each server and stores the one that accepted the login."""
    fake_cloud.home_region = "europe"
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": f" {USERNAME} ", "password": PASSWORD, CONF_REGION: "auto"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"] == {"username": USERNAME, "password": PASSWORD, CONF_REGION: "europe"}
    assert result["result"].unique_id == str(UID)


async def test_explicit_region(hass: HomeAssistant, fake_cloud, fake_broker) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": PASSWORD, CONF_REGION: "australia"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REGION] == "australia"


async def test_invalid_auth(hass: HomeAssistant, fake_cloud) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": "wrong", CONF_REGION: "auto"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect_then_recover(hass: HomeAssistant, fake_cloud, fake_broker) -> None:
    fake_cloud.fail_with = GreeApiError("timeout")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": PASSWORD, CONF_REGION: "australia"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    fake_cloud.fail_with = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": PASSWORD, CONF_REGION: "australia"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(hass: HomeAssistant, fake_cloud, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": USERNAME, "password": PASSWORD, CONF_REGION: "australia"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, fake_cloud, fake_broker, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, data={**config_entry.data, "password": "old"})
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "still-wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": PASSWORD})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == PASSWORD
