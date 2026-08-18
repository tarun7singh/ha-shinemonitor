# Changelog

## 0.2.3 — 2026-08-18

### Bug fixes

- Realtime device sensors (PV/grid voltages, currents, output power,
  temperatures, device-level `energy_today`/`energy_total`, …) were stuck on
  `unknown` for healthy inverters. The realtime fetch was gated on
  `device.status == 1`, but `status` is the **alarm** flag (`0` = normal,
  `1` = alarm) per `docs/shinemonitor-api.md` §5.4 — so a producing inverter
  with `status=0` never had its realtime data fetched. The gate now only
  checks `comStatus` (the actual "cloud is receiving fresh data" signal).
- Date-keyed API calls (realtime last-data, power curve, month-per-day) now
  use the plant's local date from the collector metadata (`timezone`, seconds
  east of UTC) instead of the HA host's date, so plants east of the host
  (e.g. IST) query the correct day/month around midnight.
- Statistics backfill no longer imports the API's future-dated filler rows
  (val=0), which rendered as a phantom zero "tomorrow" bar in the monthly
  chart. The plant's in-progress "today" row is still kept, and day/month
  selection uses the plant's local clock so IST plants keep today's bar.

## 0.2.2 — 2026-04-18

### Bug fixes

- Monthly energy bar chart now includes today's in-progress bar. The
  daily-history backfill was filtering out the plant's "today" row for
  accounts east of UTC (e.g. IST, +05:30) because it compared the API's
  local timestamp against `datetime.utcnow().date()` — so `2026-04-18
  00:00 IST` looked "later than today UTC" and got dropped. Filter
  removed; future-dated filler rows are kept in the stream but add zero
  to the cumulative sum so they don't distort the chart.

## 0.2.1 — 2026-04-18

### Bug fixes

- `sensor.<plant>_current_power` now reports `0` (not `unknown`) when the
  coordinator has successfully refreshed but the cloud has no samples yet
  for today. This lets the gauge card render and the history chart plot a
  continuous line through pre-dawn instead of showing "Entity is
  non-numeric".

## 0.2.0 — 2026-04-17

Initial public release.

### Features

- Reverse-engineered shinemonitor.com web API; documented in `docs/shinemonitor-api.md`.
- Cloud-polling HA integration with config flow (`username` + `password`),
  multi-plant selection, reauth, and options (poll interval 60–3600 s,
  default 300 s to match datalogger upload cadence).
- Plant device entities:
  - `sensor.<plant>_current_power` (W) — latest 5-minute sample
  - `sensor.<plant>_today`, `<plant>_month`, `<plant>_year`, `<plant>_lifetime` (kWh)
  - `binary_sensor.<plant>_producing` — on iff latest non-zero power sample
    is ≤10 min behind the cloud's last-report time
  - `binary_sensor.<plant>_alarm`
- Per-inverter entities (dynamically discovered via
  `queryPlantDeviceChartsFieldsByType`): PV/grid V and A per string, grid
  frequency, bus voltage, internal temps, output power, cumulative and
  waiting time, plus `binary_sensor.<sn>_online` backed by `comStatus`.
- Long-term statistics backfill: on setup and once per hour, imports
  yearly → monthly → daily kWh into the external statistic
  `shinemonitor:<plant>_energy` so monthly/yearly bar charts have history
  from plant install, not from HA install.
- Sample Lovelace dashboard in `docs/dashboard.yaml` with the three graphs
  shinemonitor.com shows: real-time power (line), monthly energy (bars),
  yearly energy (bars).
- Diagnostics with redaction of tokens, secrets, hashed password, user
  identifiers, and device identifiers.

### Known caveats

- Read-only. No parameter writes / control commands yet.
- Password is stored as its SHA-1 in the config entry — that's the
  effective credential the API accepts.
- The ShineMonitor cloud itself has small internal inconsistencies between
  `total_per_year` and summed `year_per_month` for some years (tens to
  hundreds of kWh). The monthly bar chart uses the finer-grain source.
