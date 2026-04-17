"""ShineMonitor integration entry points."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

import time

from .api import ShineAuthError, ShineClient, ShineConnectionError
from .const import CONF_PLANTID, CONF_PWD_SHA1, DOMAIN, MANUFACTURER, PLATFORMS
from .coordinator import ShineCoordinator
from .statistics import async_backfill_lifetime_statistics

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = ShineClient(
        session=session,
        usr=entry.data[CONF_USERNAME],
        pwd_sha1=entry.data[CONF_PWD_SHA1],
    )

    try:
        await client.login()
    except ShineAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ShineConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    plantid = int(entry.data[CONF_PLANTID])
    coordinator = ShineCoordinator(hass, entry, client, plantid)
    await coordinator.async_config_entry_first_refresh()

    # Pre-register the plant device so entities attached to it during platform
    # setup resolve to the correct device record (and so inverter entities'
    # ``via_device`` reference points at something that already exists).
    plant_name = (
        (coordinator.data.plant_info.get("name") if coordinator.data else None)
        or entry.title
        or f"Plant {plantid}"
    )
    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"plant:{plantid}")},
        name=plant_name,
        manufacturer=MANUFACTURER,
        model="Plant",
        configuration_url="https://shinemonitor.com/",
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Fire-and-forget initial stats backfill, and refresh at most once per hour.
    last = {"at": 0.0}

    async def _maybe_backfill() -> None:
        if time.time() - last["at"] < 3600:
            return
        try:
            await async_backfill_lifetime_statistics(hass, coordinator)
            last["at"] = time.time()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("statistics backfill failed")

    hass.async_create_task(_maybe_backfill())
    entry.async_on_unload(coordinator.async_add_listener(lambda: hass.async_create_task(_maybe_backfill())))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
