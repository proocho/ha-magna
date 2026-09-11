"""Config flow integrácie Magna iPortál."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import MagnaApi, MagnaAuthError, MagnaError, kind_for_label, point_code
from .const import (
    CONF_ANCHOR_KWH,
    CONF_ANCHOR_MONTH,
    CONF_POINT_CODE,
    CONF_POINT_LABEL,
    DOMAIN,
    KIND_CONSUMPTION,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class MagnaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Prihlásenie do iPortálu a výber odberného miesta."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MagnaOptionsFlow:
        return MagnaOptionsFlow()

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._points: list[str] = []

    async def _async_points(self, username: str, password: str) -> list[str]:
        """Prihlási sa a vráti labely odberných miest."""
        session = async_create_clientsession(self.hass)
        api = MagnaApi(session, username, password)
        try:
            await api.async_login()
            return [label for _, label in await api.async_points()]
        finally:
            await api.async_logout()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            try:
                points = await self._async_points(self._username, self._password)
            except MagnaAuthError:
                errors["base"] = "invalid_auth"
            except MagnaError as err:
                _LOGGER.debug("iPortál neodpovedal: %s", err)
                errors["base"] = "cannot_connect"
            else:
                # Požičovňa a prebytok výroby nie sú samostatné odberné miesta,
                # ale sprievodné rady k účtu -- koordinátor si ich nájde sám.
                # Vyberá sa len skutočné odberné miesto spotreby.
                self._points = [
                    p for p in points if kind_for_label(p) == KIND_CONSUMPTION
                ]
                if not self._points:
                    return self.async_abort(reason="no_points")
                if len(self._points) == 1:
                    return await self._async_create(self._points[0])
                return await self.async_step_point()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_point(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Výber odberného miesta, keď ich je na účte viac."""
        if user_input is not None:
            return await self._async_create(user_input[CONF_POINT_LABEL])
        return self.async_show_form(
            step_id="point",
            data_schema=vol.Schema(
                {vol.Required(CONF_POINT_LABEL): vol.In(self._points)}
            ),
        )

    async def _async_create(self, label: str) -> ConfigFlowResult:
        kod = point_code(label)
        # Identita je EIC kód, nie prihlasovacie meno -- na jednom účte môže
        # byť viac odberných miest a každé je vlastný config entry.
        await self.async_set_unique_id(f"{self._username.strip().lower()}:{kod}")
        self._abort_if_unique_id_configured()
        # Nazov bez EIC kodu -- z neho sa odvodzuju entity_id a
        # "sensor.24zzs40002004760_petrova_ves_418_418_..." sa neda citat.
        adresa = label.split(" - ", 1)[-1].split(",")[0].strip()
        return self.async_create_entry(
            title=adresa or label,
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_POINT_CODE: kod,
                CONF_POINT_LABEL: label,
            },
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
                await self._async_points(username, user_input[CONF_PASSWORD])
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


class MagnaOptionsFlow(OptionsFlow):
    """Ukotvenie zostatku požičovne z faktúry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        teraz = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ANCHOR_KWH,
                        description={
                            "suggested_value": teraz.get(CONF_ANCHOR_KWH)
                        },
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="kWh",
                        )
                    ),
                    vol.Optional(
                        CONF_ANCHOR_MONTH,
                        description={
                            "suggested_value": teraz.get(CONF_ANCHOR_MONTH)
                        },
                    ): selector.DateSelector(),
                }
            ),
        )
