"""Integrácia Magna iPortál -- spotreba, prebytok a požičovňa elektriny."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import MagnaApi
from .const import CONF_POINT_CODE, CONF_POINT_LABEL
from .coordinator import MagnaCoordinator

PLATFORMS = [Platform.SENSOR]

type MagnaConfigEntry = ConfigEntry[MagnaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MagnaConfigEntry) -> bool:
    """Nastaví integráciu z config entry."""
    # Vlastná session -- portál drží prihlásenie v PHPSESSID cookie.
    session = async_create_clientsession(hass)
    api = MagnaApi(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    coordinator = MagnaCoordinator(
        hass,
        entry,
        api,
        code=entry.data[CONF_POINT_CODE],
        label=entry.data[CONF_POINT_LABEL],
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    # Zmena ukotvenia meni aj to, ci senzor zostatku vobec existuje.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: MagnaConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MagnaConfigEntry) -> bool:
    """Odpojí integráciu."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.api.async_logout()
    return unloaded
