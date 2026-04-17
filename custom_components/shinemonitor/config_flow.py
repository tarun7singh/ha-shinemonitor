"""Config flow for ShineMonitor."""

from __future__ import annotations

import logging
from collections.abc import Mapping
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ShineAuthError, ShineClient, ShineConnectionError
from .const import (
    CONF_PLANT_NAME,
    CONF_PLANTID,
    CONF_PWD_SHA1,
    CONF_SCAN_INTERVAL,
    CONF_UID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ShineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ShineMonitor."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._pwd_sha1: str | None = None
        self._uid: int | None = None
        self._plants: list[dict[str, Any]] = []
        self._reauth_entry: ConfigEntry | None = None

    async def _try_login(self, username: str, password: str) -> tuple[int, list[dict[str, Any]]]:
        session = async_get_clientsession(self.hass)
        pwd_sha1 = ShineClient.hash_password(password)
        client = ShineClient(session=session, usr=username, pwd_sha1=pwd_sha1)
        sess = await client.login()
        plants = await client.query_plants()
        return sess.uid, plants

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]

            try:
                uid, plants = await self._try_login(username, password)
            except ShineAuthError:
                errors["base"] = "invalid_auth"
            except ShineConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error during ShineMonitor login")
                errors["base"] = "unknown"
            else:
                if not plants:
                    errors["base"] = "no_plants"
                else:
                    self._username = username
                    self._pwd_sha1 = ShineClient.hash_password(password)
                    self._uid = uid
                    self._plants = plants
                    if len(plants) == 1:
                        return await self._finish(plants[0])
                    return await self.async_step_plant()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_plant(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = {
            str(p["pid"]): p.get("pname", f"Plant {p['pid']}") for p in self._plants
        }
        schema = vol.Schema({vol.Required(CONF_PLANTID): vol.In(options)})

        if user_input is not None:
            pid = int(user_input[CONF_PLANTID])
            plant = next(p for p in self._plants if int(p["pid"]) == pid)
            return await self._finish(plant)

        return self.async_show_form(step_id="plant", data_schema=schema)

    async def _finish(self, plant: dict[str, Any]) -> ConfigFlowResult:
        assert self._username is not None
        assert self._pwd_sha1 is not None
        assert self._uid is not None

        pid = int(plant["pid"])
        unique_id = f"{self._uid}:{pid}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        data = {
            CONF_USERNAME: self._username,
            CONF_PWD_SHA1: self._pwd_sha1,
            CONF_UID: self._uid,
            CONF_PLANTID: pid,
            CONF_PLANT_NAME: plant.get("pname", f"Plant {pid}"),
        }
        return self.async_create_entry(title=data[CONF_PLANT_NAME], data=data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._username = entry_data.get(CONF_USERNAME)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None

        if user_input is not None and self._username is not None:
            password = user_input[CONF_PASSWORD]
            try:
                await self._try_login(self._username, password)
            except ShineAuthError:
                errors["base"] = "invalid_auth"
            except ShineConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                new_data = {
                    **self._reauth_entry.data,
                    CONF_PWD_SHA1: ShineClient.hash_password(password),
                }
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry, data=new_data
                )
                await self.hass.config_entries.async_reload(
                    self._reauth_entry.entry_id
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": self._username or ""},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return ShineOptionsFlow(entry)


class ShineOptionsFlow(OptionsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
