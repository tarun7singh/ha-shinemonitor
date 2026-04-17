# ShineMonitor Solar — Home Assistant integration

Cloud-polling integration for solar PV installations that report to
[shinemonitor.com](https://shinemonitor.com/). Exposes plant-level energy
counters, per-device real-time telemetry (PV strings, grid phases, inverter
temperatures, bus voltage, grid frequency), online/communicating status, and
alarms.

Works with single-phase and three-phase grid-tie inverters that the shinemonitor.com
web dashboard can already see. Read-only in v1 — no parameter writes.

## Install (HACS custom repository)

1. HACS → Integrations → ⋮ → **Custom repositories**.
2. Add `https://github.com/Tarun7singh/ha-shinemonitor` as category *Integration*.
3. Install **ShineMonitor Solar**.
4. Restart Home Assistant.
5. Settings → Devices & Services → **Add Integration** → *ShineMonitor Solar*.
6. Enter your shinemonitor.com username and password.

## What you get

### Per plant
- `sensor.<plant>_energy_today` (kWh, `energy` / `total_increasing`)
- `sensor.<plant>_energy_total` (kWh, `energy` / `total_increasing`)
- `binary_sensor.<plant>_alarm` (`problem` device class; attributes expose the latest alarm)

The two energy sensors are what you plug into the **Energy Dashboard** as solar
production.

### Per device (one per inverter)
Dynamically discovered from `queryPlantDeviceChartsFieldsByType`. Typical set
for a grid-tie inverter:
- PV1/PV2/PV3 voltage (V) and current (A)
- Grid R/S/T voltage (V) and current (A)
- Grid line voltage RS/ST/TR (V)
- Grid frequency (Hz), bus voltage (V)
- Internal ambient and radiator temperature (°C)
- Output power (W), output apparent/reactive power (VA)
- `binary_sensor.<sn>_online` — device.status
- `binary_sensor.<sn>_communicating` — device.comStatus (fresh data vs. stale)

## Options

- **Poll interval (seconds)** — default 300 to match the datalogger's 5-minute
  upload cadence. Values below ~300 s will not give you fresher data; the cloud
  only has what the datalogger uploaded.

## Reauthentication

If the cloud session expires or your password changes, HA will raise a
"Re-authentication required" notification. Follow it and re-enter your
password.

## Known limitations

- **Read-only.** No control of inverter parameters in v1.
- **Password is stored as SHA-1.** This is the effective credential the API
  accepts — rotate your shinemonitor.com password if you are worried about
  it being compromised from the HA config dir.
- **`datFetch=300`.** The shinemonitor cloud only refreshes roughly every
  5 minutes, so HA entities update at the same rate even if you poll faster.
- **`comStatus=0` → `ERR_NO_RECORD`.** When the inverter/datalogger is offline
  (typical at night), the realtime fields are unavailable. Plant-level energy
  sensors fall back to `queryPlantDeviceDesignatedInformation`, which stays
  available.

## How it works

Reverse-engineered from the shinemonitor.com web UI. The full technical
reference — signing scheme, every endpoint, request/response schemas,
captured payloads — lives in [`docs/shinemonitor-api.md`](docs/shinemonitor-api.md).

Polling flow per tick:
1. `queryPlantDeviceStatus` — online/offline state of each device.
2. `queryDeviceRealLastData` per device with `comStatus=1` — instantaneous sensors.
3. `queryPlantDeviceDesignatedInformation` — reliable energy_today / energy_total.
4. `webQueryPlantsWarning` — alarms.

## Credits

Architecture inspired by
[`andreas-glaser/ha-dessmonitor`](https://github.com/andreas-glaser/ha-dessmonitor)
(MIT, same Eybond backend, different portal/action vocabulary). This
integration implements the shinemonitor.com action set fresh.

## License

MIT — see [LICENSE](LICENSE).
