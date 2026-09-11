"""Config flow integrácie Magna iPortál."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import MagnaApi, MagnaAuthError, MagnaError, kind_for_label
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class MagnaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Prihlásenie do iPortálu."""

    VERSION = 1

    async def _async_check(self, username: str, password: str) -> list[str]:
        """Prihlási sa a vráti zoznam odberných miest."""
        session = async_create_clientsession(self.hass)
        api = MagnaApi(session, username, password)
        try:
            await api.async_login()
            return await api.async_points()
        finally:
            await api.async_logout()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                points = await self._async_check(username, password)
            except MagnaAuthError:
                errors["base"] = "invalid_auth"
            except MagnaError as err:
                _LOGGER.debug("iPortál neodpovedal: %s", err)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(username.strip().lower())
                self._abort_if_unique_id_configured()
                druhy = sorted({kind_for_label(p) for p in points})
                _LOGGER.debug("nájdené odberné miesta: %s (druhy %s)", points, druhy)
                return self.async_create_entry(
                    title=f"Magna ({username})",
                    data={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            username = entry.data[CONF_USERNAME]
            try:
                await self._async_check(username, user_input[CONF_PASSWORD])
            except MagnaAuthError:
                errors["base"] = "invalid_auth"
            except MagnaError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
