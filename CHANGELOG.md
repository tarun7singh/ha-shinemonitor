# Changelog

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
