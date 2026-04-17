"""Constants for the ShineMonitor integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "shinemonitor"
MANUFACTURER: Final = "ShineMonitor"

BASE_URL: Final = "https://web.shinemonitor.com/public/"
COMPANY_KEY: Final = "bnrl_frRFjEz8Mkn"

CONF_PWD_SHA1: Final = "pwd_sha1"
CONF_PLANTID: Final = "plantid"
CONF_PLANT_NAME: Final = "plant_name"
CONF_UID: Final = "uid"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 300
MIN_SCAN_INTERVAL: Final = 60
MAX_SCAN_INTERVAL: Final = 3600

TOKEN_REFRESH_BUFFER: Final = 600

DEVTYPE_GRID_TIE: Final = 512

ERR_NONE: Final = 0
ERR_NO_RECORD: Final = 12
ERR_NOT_FOUND_DEVICE_WARNING: Final = 264

AUTH_ERROR_CODES: Final = frozenset(
    {
        2,
        3,
        4,
        5,
        10006,
        10007,
    }
)

FIELD_MAP: Final[dict[str, dict[str, str]]] = {
    "eybond_read_28": {"name": "PV1 voltage", "unit": "V"},
    "eybond_read_29": {"name": "PV1 current", "unit": "A"},
    "eybond_read_30": {"name": "PV2 voltage", "unit": "V"},
    "eybond_read_31": {"name": "PV2 current", "unit": "A"},
    "eybond_read_32": {"name": "PV3 voltage", "unit": "V"},
    "eybond_read_33": {"name": "PV3 current", "unit": "A"},
    "eybond_read_34": {"name": "Grid R voltage", "unit": "V"},
    "eybond_read_35": {"name": "Grid R current", "unit": "A"},
    "eybond_read_36": {"name": "Grid S voltage", "unit": "V"},
    "eybond_read_37": {"name": "Grid S current", "unit": "A"},
    "eybond_read_38": {"name": "Grid T voltage", "unit": "V"},
    "eybond_read_39": {"name": "Grid T current", "unit": "A"},
    "eybond_read_40": {"name": "Grid line voltage RS", "unit": "V"},
    "eybond_read_41": {"name": "Grid line voltage ST", "unit": "V"},
    "eybond_read_42": {"name": "Grid line voltage TR", "unit": "V"},
    "eybond_read_43": {"name": "Grid frequency", "unit": "Hz"},
    "eybond_read_44": {"name": "Bus voltage", "unit": "V"},
    "eybond_read_49": {"name": "Internal ambient temperature", "unit": "°C"},
    "eybond_read_50": {"name": "Internal radiator temperature", "unit": "°C"},
    "eybond_read_16": {"name": "Output apparent power", "unit": "VA"},
    "eybond_read_17": {"name": "Output reactive power", "unit": "VA"},
    "eybond_read_20": {"name": "Instrument power", "unit": "W"},
    "eybond_read_23": {"name": "Cumulative operating time", "unit": "h"},
    "eybond_read_25": {"name": "Waiting time", "unit": "s"},
    "output_power": {"name": "Output power", "unit": "W"},
    "energy_today": {"name": "Energy today", "unit": "kWh"},
    "energy_total": {"name": "Energy total", "unit": "kWh"},
}

PLATFORMS: Final = ["sensor", "binary_sensor"]
