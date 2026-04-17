"""Backfill Home Assistant long-term statistics from ShineMonitor history.

The monthly/yearly graph cards need historical data points or they stay empty
for 30/365 days after installation. We push the API's historical kWh series
into HA as **external statistics** under ``shinemonitor:<plant>_energy`` so
the Statistics Graph card can render full bar charts immediately while
leaving the live ``<plant>_lifetime`` sensor's own history untouched.

Granularity strategy (coarsest first — later writes override):
1. ``queryPlantEnergyTotalPerYear``  → one hourly point per year.
2. ``queryPlantEnergyYearPerMonth``  → one hourly point per month in recent years.
3. ``queryPlantEnergyMonthPerDay``   → one hourly point per day in recent months.

All points are cumulative sums in kWh — HA's ``change`` statistic type derives
the per-period delta from consecutive cumulative values.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .api import ShineApiError, ShineClient, ShineConnectionError
from .const import DOMAIN
from .coordinator import ShineCoordinator

_LOGGER = logging.getLogger(__name__)


def _ts(raw: str) -> datetime | None:
    try:
        # API timestamps are in the plant's local timezone. Home Assistant
        # statistics use UTC-aligned hour boundaries — we treat them as UTC
        # directly rather than trying to convert, since the graphs are aligned
        # to the plant's "day" concept anyway.
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def _historical_daily_kwh(
    client: ShineClient, plantid: int, months_back: int
) -> list[tuple[datetime, float]]:
    """Fetch daily kWh for the last ``months_back`` months (including current)."""
    today = datetime.now(timezone.utc).date()
    out: list[tuple[datetime, float]] = []
    year, month = today.year, today.month
    for _ in range(months_back):
        stamp = f"{year:04d}-{month:02d}"
        try:
            rows = await client.query_plant_energy_month_per_day(plantid, stamp)
        except (ShineApiError, ShineConnectionError) as err:
            _LOGGER.debug("month_per_day(%s) failed: %s", stamp, err)
            rows = []
        for r in rows:
            ts = _ts(r.get("ts", ""))
            val = _num(r.get("val"))
            if ts is None or val is None:
                continue
            if ts.date() > today:
                continue  # future-dated zero filler rows
            out.append((ts, val))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return out


async def _historical_monthly_kwh(
    client: ShineClient, plantid: int, years_back: int
) -> list[tuple[datetime, float]]:
    today = datetime.now(timezone.utc).date()
    out: list[tuple[datetime, float]] = []
    for year in range(today.year, today.year - years_back, -1):
        try:
            rows = await client.query_plant_energy_year_per_month(plantid, str(year))
        except (ShineApiError, ShineConnectionError) as err:
            _LOGGER.debug("year_per_month(%s) failed: %s", year, err)
            rows = []
        for r in rows:
            ts = _ts(r.get("ts", ""))
            val = _num(r.get("val"))
            if ts is None or val is None:
                continue
            if ts.year > today.year or (ts.year == today.year and ts.month > today.month):
                continue
            out.append((ts, val))
    return out


async def _historical_yearly_kwh(
    client: ShineClient, plantid: int
) -> list[tuple[datetime, float]]:
    try:
        rows = await client.query_plant_energy_total_per_year(plantid)
    except (ShineApiError, ShineConnectionError) as err:
        _LOGGER.debug("total_per_year failed: %s", err)
        return []
    out: list[tuple[datetime, float]] = []
    for r in rows:
        ts = _ts(r.get("ts", ""))
        val = _num(r.get("val"))
        if ts is None or val is None:
            continue
        out.append((ts, val))
    return out


def statistic_id(plant_name: str) -> str:
    """External-statistic id used by the Lovelace cards."""
    return f"{DOMAIN}:{slugify(plant_name)}_energy"


async def async_backfill_lifetime_statistics(
    hass: HomeAssistant,
    coordinator: ShineCoordinator,
) -> None:
    """Push historical kWh into the ``shinemonitor:<plant>_energy`` stream.

    Called opportunistically: every successful refresh. The operation is
    idempotent — re-importing an hour overwrites the previous value for that
    hour.
    """
    if coordinator.data is None:
        return
    plant_name = coordinator.data.plant_info.get("name") or f"plant_{coordinator.plantid}"
    stat_id = statistic_id(plant_name)

    # Build combined daily->monthly->yearly series, finest granularity wins.
    # Each tuple is (period_start, kwh_in_period).
    series_by_start: dict[datetime, float] = {}

    for ts, val in await _historical_yearly_kwh(coordinator.client, coordinator.plantid):
        series_by_start[ts] = val
    for ts, val in await _historical_monthly_kwh(coordinator.client, coordinator.plantid, years_back=5):
        series_by_start[ts] = val
    for ts, val in await _historical_daily_kwh(coordinator.client, coordinator.plantid, months_back=12):
        series_by_start[ts] = val

    if not series_by_start:
        _LOGGER.debug("no historical rows to backfill for %s", stat_id)
        return

    # Deduplication is handled by dict-overwrite in the write loop above:
    # yearly writes first, monthly overwrites where it overlaps (YYYY-01-01),
    # daily overwrites where it overlaps (YYYY-MM-01). Each (date) key ends
    # up with the finest granularity available, so no extra pruning is needed.

    # Convert per-period totals into a cumulative series and build StatisticData points.
    ordered = sorted(series_by_start.items())
    cumulative = 0.0
    stats: list[StatisticData] = []
    for start, increment in ordered:
        cumulative += float(increment)
        stats.append(
            StatisticData(
                start=start.replace(minute=0, second=0, microsecond=0),
                state=cumulative,
                sum=cumulative,
            )
        )

    metadata = StatisticMetaData(
        has_mean=False,
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"{plant_name} energy",
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class="energy",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )

    _LOGGER.debug("importing %d stat points for %s", len(stats), stat_id)
    async_add_external_statistics(hass, metadata, stats)
