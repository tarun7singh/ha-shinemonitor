"""HTTP client for the ShineMonitor cloud API.

The signing scheme and endpoint contract are documented in
``docs/shinemonitor-api.md``. Any behavioural change here should be
reflected there and vice versa.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    AUTH_ERROR_CODES,
    BASE_URL,
    COMPANY_KEY,
    ERR_NONE,
    ERR_NO_RECORD,
    ERR_NOT_FOUND_DEVICE_WARNING,
    TOKEN_REFRESH_BUFFER,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class ShineApiError(Exception):
    """Non-zero ``err`` from the API that is not specifically handled."""

    def __init__(self, err: int, desc: str, action: str) -> None:
        super().__init__(f"{action} failed: err={err} desc={desc}")
        self.err = err
        self.desc = desc
        self.action = action


class ShineAuthError(ShineApiError):
    """Credentials or token were rejected."""


class ShineConnectionError(Exception):
    """Transport-level failure (DNS, TCP, TLS, timeout, bad JSON)."""


@dataclass
class ShineSession:
    """In-memory copy of the auth response."""

    secret: str
    token: str
    uid: int
    expires_at: float
    usr: str
    role: int = 0

    def is_fresh(self, buffer: float = TOKEN_REFRESH_BUFFER) -> bool:
        return time.time() < (self.expires_at - buffer)


@dataclass
class ShineClient:
    """Stateless-ish wrapper around ``/public/`` — holds the session."""

    session: aiohttp.ClientSession
    usr: str
    pwd_sha1: str
    company_key: str = COMPANY_KEY
    base_url: str = BASE_URL

    _session: ShineSession | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @staticmethod
    def hash_password(password: str) -> str:
        """SHA-1 hex of the user's password. This value is the effective credential."""
        return hashlib.sha1(password.encode("utf-8")).hexdigest()

    @staticmethod
    def _sha1(s: str) -> str:
        return hashlib.sha1(s.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_params(params: list[tuple[str, str]]) -> str:
        # The server verifies the signature against the raw query-string suffix.
        # Reproduce the browser's encoding: keep ',' and ':' literal, space -> %20.
        return urllib.parse.urlencode(params, safe=",:", quote_via=urllib.parse.quote)

    def _build_url(
        self,
        action: str,
        extra: list[tuple[str, str]],
        *,
        authenticated: bool,
    ) -> str:
        salt = str(int(time.time() * 1000))
        # The captured auth call signs only the action params; post-auth calls
        # include i18n/lang in both the URL and the signed suffix.
        if authenticated:
            body = [("action", action), *extra, ("i18n", "en_US"), ("lang", "en_US")]
        else:
            body = [("action", action), *extra]
        suffix = "&" + self._encode_params(body)

        if authenticated:
            assert self._session is not None
            sign = self._sha1(salt + self._session.secret + self._session.token + suffix)
            head = f"sign={sign}&salt={salt}&token={self._session.token}"
        else:
            sign = self._sha1(salt + self.pwd_sha1 + suffix)
            head = f"sign={sign}&salt={salt}"

        return f"{self.base_url}?{head}{suffix}"

    async def _request(self, url: str, action: str) -> dict[str, Any]:
        try:
            async with self.session.get(url, timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                body: dict[str, Any] = await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise ShineConnectionError(f"timeout calling {action}") from err
        except aiohttp.ClientError as err:
            raise ShineConnectionError(f"transport error calling {action}: {err}") from err
        except ValueError as err:
            raise ShineConnectionError(f"invalid JSON from {action}: {err}") from err

        err_code = int(body.get("err", -1))
        desc = str(body.get("desc", ""))

        if err_code == ERR_NONE:
            return body.get("dat") or {}
        if err_code == ERR_NO_RECORD:
            return {}
        if err_code == ERR_NOT_FOUND_DEVICE_WARNING:
            return {}
        if err_code in AUTH_ERROR_CODES:
            raise ShineAuthError(err_code, desc, action)
        raise ShineApiError(err_code, desc, action)

    async def login(self) -> ShineSession:
        """Perform the ``auth`` call and cache the resulting session."""
        extras = [
            ("usr", self.usr),
            ("company-key", self.company_key),
        ]
        url = self._build_url("auth", extras, authenticated=False)
        dat = await self._request(url, "auth")

        try:
            expire = float(dat["expire"])
            sess = ShineSession(
                secret=str(dat["secret"]),
                token=str(dat["token"]),
                uid=int(dat["uid"]),
                expires_at=time.time() + expire,
                usr=str(dat.get("usr", self.usr)),
                role=int(dat.get("role", 0)),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise ShineAuthError(-1, f"malformed auth payload: {dat!r}", "auth") from err

        self._session = sess
        _LOGGER.debug("auth ok uid=%s token=***%s expire=%ss", sess.uid, sess.token[-4:], expire)
        return sess

    async def ensure_valid_session(self) -> ShineSession:
        """Log in if there is no cached session or the cached one is near expiry."""
        async with self._lock:
            if self._session is None or not self._session.is_fresh():
                await self.login()
            assert self._session is not None
            return self._session

    def load_session(self, session: ShineSession) -> None:
        """Restore a previously persisted session (e.g. from HA storage)."""
        self._session = session

    @property
    def session_snapshot(self) -> ShineSession | None:
        return self._session

    async def _call(self, action: str, **params: Any) -> Any:
        await self.ensure_valid_session()
        extras = [(k, _stringify(v)) for k, v in params.items() if v is not None]
        url = self._build_url(action, extras, authenticated=True)
        try:
            return await self._request(url, action)
        except ShineAuthError:
            # Token may have been invalidated server-side; retry once after fresh login.
            _LOGGER.debug("token rejected during %s; re-authenticating", action)
            self._session = None
            await self.ensure_valid_session()
            url = self._build_url(action, extras, authenticated=True)
            return await self._request(url, action)

    # ------------------------------------------------------------------ #
    # Endpoint wrappers.  See docs/shinemonitor-api.md §5 for the contract.
    # ------------------------------------------------------------------ #

    async def query_plants(self) -> list[dict[str, Any]]:
        dat = await self._call("queryPlantsInfo")
        return list(dat.get("info", [])) if isinstance(dat, dict) else []

    async def query_plant_info(self, plantid: int) -> dict[str, Any]:
        dat = await self._call("queryPlantInfo", plantid=plantid)
        return dat if isinstance(dat, dict) else {}

    async def query_plant_device_status(self, plantid: int) -> dict[str, Any]:
        dat = await self._call("queryPlantDeviceStatus", plantid=plantid)
        return dat if isinstance(dat, dict) else {}

    async def query_collectors(
        self, plantid: int, page: int = 0, pagesize: int = 20
    ) -> list[dict[str, Any]]:
        dat = await self._call(
            "queryCollectors", plantid=plantid, pn="", page=page, pagesize=pagesize
        )
        return list(dat.get("collector", [])) if isinstance(dat, dict) else []

    async def query_plant_device_charts_fields_by_type(
        self, plantid: int, devtype: int, devcode: int, type_: int = 1
    ) -> list[dict[str, Any]]:
        dat = await self._call(
            "queryPlantDeviceChartsFieldsByType",
            plantid=plantid,
            devtype=devtype,
            devcode=devcode,
            type=type_,
        )
        return list(dat) if isinstance(dat, list) else []

    async def query_device_real_last_data(
        self,
        *,
        pn: str,
        devcode: int,
        sn: str,
        devaddr: int,
        date: str,
    ) -> dict[str, Any]:
        dat = await self._call(
            "queryDeviceRealLastData",
            devaddr=devaddr,
            pn=pn,
            devcode=devcode,
            sn=sn,
            date=date,
        )
        return dat if isinstance(dat, dict) else {}

    async def query_plant_device_designated_information(
        self, plantid: int, devtype: int, parameter: str
    ) -> list[dict[str, Any]]:
        dat = await self._call(
            "queryPlantDeviceDesignatedInformation",
            plantid=plantid,
            devtype=devtype,
            parameter=parameter,
        )
        if isinstance(dat, dict):
            return list(dat.get("device", []))
        return []

    async def query_plant_energy_month_per_day(
        self, plantid: int, yyyy_mm: str
    ) -> list[dict[str, Any]]:
        dat = await self._call(
            "queryPlantEnergyMonthPerDay", plantid=plantid, date=yyyy_mm
        )
        return list(dat.get("perday", [])) if isinstance(dat, dict) else []

    async def query_plant_energy_year_per_month(
        self, plantid: int, yyyy: str
    ) -> list[dict[str, Any]]:
        dat = await self._call(
            "queryPlantEnergyYearPerMonth", plantid=plantid, date=yyyy
        )
        return list(dat.get("permonth", [])) if isinstance(dat, dict) else []

    async def query_plant_energy_total_per_year(
        self, plantid: int
    ) -> list[dict[str, Any]]:
        dat = await self._call("queryPlantEnergyTotalPerYear", plantid=plantid)
        return list(dat.get("peryear", [])) if isinstance(dat, dict) else []

    async def query_plant_active_output_power_one_day(
        self, plantid: int, date: str
    ) -> list[dict[str, Any]]:
        """Today's 5-minute power samples.

        Response shape: ``{"outputPower": [{"val":"123", "ts":"YYYY-MM-DD HH:MM:SS"}, ...],
        "activePowerSwitch": ...}``.  Returns just the ``outputPower`` list.
        """
        dat = await self._call(
            "queryPlantActiveOuputPowerOneDay", plantid=plantid, date=date
        )
        if isinstance(dat, dict):
            return list(dat.get("outputPower", []))
        if isinstance(dat, list):
            return list(dat)
        return []

    async def web_query_plants_warning(
        self,
        plantid: int,
        *,
        handle: bool = False,
        page: int = 0,
        pagesize: int = 10,
    ) -> list[dict[str, Any]]:
        dat = await self._call(
            "webQueryPlantsWarning",
            mode="strict",
            plantid=plantid,
            handle=str(handle).lower(),
            pn="",
            page=page,
            pagesize=pagesize,
        )
        if isinstance(dat, dict):
            return list(dat.get("warning", []) or dat.get("info", []))
        if isinstance(dat, list):
            return list(dat)
        return []


def _stringify(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
