"""Diagnostics support for ShineMonitor."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PWD_SHA1, CONF_UID, DOMAIN
from .coordinator import ShineCoordinator

REDACT_ENTRY = {CONF_PWD_SHA1, CONF_UID, "password", "username", "usr"}
REDACT_DATA = {
    "pn",
    "sn",
    "cpn",
    "uid",
    "usr",
    "token",
    "secret",
    "picBig",
    "picSmall",
    "lat",
    "lon",
    "address",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: ShineCoordinator = hass.data[DOMAIN][entry.entry_id]
    raw = coordinator.data
    snapshot = asdict(raw) if raw is not None and is_dataclass(raw) else {}

    session = coordinator.client.session_snapshot
    session_view = (
        {
            "uid": "**REDACTED**",
            "expires_in": int(session.expires_at - __import__("time").time()),
            "role": session.role,
        }
        if session is not None
        else None
    )

    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)},
            REDACT_ENTRY,
        ),
        "session": session_view,
        "coordinator": async_redact_data(snapshot, REDACT_DATA),
    }
