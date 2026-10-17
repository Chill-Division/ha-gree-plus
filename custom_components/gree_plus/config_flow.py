"""Config flow for Gree+."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import REGIONS, GreeApiError, GreeAuthError, GreeCloudApi
from .const import CONF_REGION, DOMAIN, LOGGER

AUTO_REGION = "auto"


class GreePlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in to a Gree+ account."""

    VERSION = 1

    async def _login(self, username: str, password: str, region: str) -> tuple[str, int]:
        """Return (region, uid). Tries every server when ``region`` is auto."""
        session = async_get_clientsession(self.hass)
        candidates = list(REGIONS) if region == AUTO_REGION else [region]
        auth_error: GreeAuthError | None = None
        for candidate in candidates:
            try:
                uid = await GreeCloudApi(session, candidate, username, password).login()
            except GreeAuthError as err:
                auth_error = err
                continue
            return candidate, uid
        raise auth_error or GreeAuthError("login failed")

    def _schema(self, defaults: Mapping[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                ),
                vol.Required(CONF_REGION, default=defaults.get(CONF_REGION, AUTO_REGION)): SelectSelector(
                    SelectSelectorConfig(
                        options=[AUTO_REGION, *REGIONS],
                        translation_key="region",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Gree+ account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            try:
                region, uid = await self._login(username, user_input[CONF_PASSWORD], user_input[CONF_REGION])
            except GreeAuthError:
                errors["base"] = "invalid_auth"
            except GreeApiError:
                LOGGER.exception("Could not reach the Gree+ cloud")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(str(uid))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD], CONF_REGION: region},
                )
        return self.async_show_form(step_id="user", data_schema=self._schema(user_input or {}), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the stored password stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                _, uid = await self._login(
                    entry.data[CONF_USERNAME], user_input[CONF_PASSWORD], entry.data[CONF_REGION]
                )
            except GreeAuthError:
                errors["base"] = "invalid_auth"
            except GreeApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(str(uid))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                    )
                }
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
