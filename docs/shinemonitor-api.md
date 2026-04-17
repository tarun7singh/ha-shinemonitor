# ShineMonitor Web API — Reverse-Engineered Reference

Technical documentation of the web dashboard at `https://shinemonitor.com/`, produced by inspecting live traffic with Chrome DevTools on 2026-04-17. Intended as the implementation spec for a Home Assistant integration (and a reusable Python/JS client).

The underlying service is the Eybond "Shine" cloud (shared by WatchPower/SmartESS/SolarPower/Dessmonitor). The same URL and signing scheme work across those domains — only `company-key` changes.

---

## 1. High-level architecture

| Concern | Value |
|---|---|
| API host | `https://web.shinemonitor.com/public/` |
| Method | `GET` only (in all observed traffic) |
| Encoding | Query-string parameters |
| Response | `application/json; charset=utf-8`, gzipped |
| Auth scheme | Custom HMAC-style using SHA-1 of `salt + secret + token + action_suffix` |
| Bootstrap host | `https://aam.eybond.com/ws/` (one-time domain config) |
| CORS | Permissive (`Access-Control-Allow-Origin: *`) |
| Server header | `nginx/1.9.6` |
| Action echo | Every response carries `x-eybond-action: <action>` header |

The UI is a classic multi-page jQuery app. The dashboard polls a fixed set of endpoints per page; the client is stateless apart from `localStorage`.

### Canonical response envelope

```json
{ "err": 0, "desc": "ERR_NONE", "dat": { ... } }
```

| `err` | `desc` | Meaning |
|---|---|---|
| `0` | `ERR_NONE` | Success. `dat` is populated. |
| `12` | `ERR_NO_RECORD` | No data for this query (e.g. inverter offline, or range empty). Not an error — treat as empty result. |
| `264` | `ERR_NOT_FOUND_DEVICE_WARNING` | No active alarms. |
| non-zero | varies | Surface `desc` to user; re-auth on token-expiry codes. |

---

## 2. Request signing

Every request is authenticated by a SHA-1 `sign` parameter. The server recomputes the signature and compares — any param tampering fails.

### 2.1 Canonical request shape

```
GET https://web.shinemonitor.com/public/?sign=<sha1>&salt=<ms>&[token=<hex>&]action=<action>&<other params>&i18n=en_US&lang=en_US
```

Rules:
- `salt` = `Date.now()` as a millisecond string.
- Every param you include in the URL must also be in the signed string, in the **same order**.
- `token` is **not** present in the URL for auth; for every other call it appears right after `salt`.

### 2.2 Signing formulae

**Login (no token yet):**

```
pwdSha1 = SHA1(password_utf8)                                    # hex
action_suffix = "&action=auth&usr=" + USR + "&company-key=" + CK
sign = SHA1(salt + pwdSha1 + action_suffix)                      # hex
```

**All other calls (post-auth):**

```
action_suffix = "&action=<action>&<k1=v1>&<k2=v2>&i18n=en_US&lang=en_US"
sign = SHA1(salt + secret + token + action_suffix)               # hex
```

The `action_suffix` is literally the query-string tail starting with `&action=` — byte-for-byte what appears in the URL after `&token=…`. URL-encoding rules match the wire (spaces → `%20`, `,` stays `,`, `:` stays `:`).

### 2.3 Verified reference values

These were captured and the SHA-1 was re-computed client-side to confirm the formulae:

| Call | Inputs | Expected sign |
|---|---|---|
| Auth | `salt=1776372088553`, `pwdSha1=&lt;sha1-of-password&gt;`, `suffix=&action=auth&usr=&lt;your-username&gt;&company-key=bnrl_frRFjEz8Mkn` | `&lt;expected-auth-sign&gt;` ✅ |
| Data | `salt=1776372097637`, `secret=&lt;secret-40-hex&gt;`, `token=8441…6299a4`, `suffix=&action=queryPlantsInfo&i18n=en_US&lang=en_US` | `&lt;expected-data-sign&gt;` ✅ |

### 2.4 Reference implementation (Python)

```python
import hashlib, time, urllib.parse, httpx

BASE = "https://web.shinemonitor.com/public/"
COMPANY_KEY = "bnrl_frRFjEz8Mkn"   # for shinemonitor.com domain

def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def build_url(params: dict, *, secret: str | None, token: str | None, pwd_sha1: str | None) -> str:
    salt = str(int(time.time() * 1000))
    # Order matters — always: action first, then action params, then i18n/lang last.
    suffix = "&" + urllib.parse.urlencode(params, safe=",: ")
    if token is None:                                # login
        sign = sha1_hex(salt + pwd_sha1 + suffix)
        head = f"sign={sign}&salt={salt}"
    else:                                            # post-auth
        sign = sha1_hex(salt + secret + token + suffix)
        head = f"sign={sign}&salt={salt}&token={token}"
    return f"{BASE}?{head}{suffix}"
```

---

## 3. Authentication flow

### 3.1 Boot sequence observed

Before the login form is even submitted the page makes three bootstrap calls:

1. `GET https://hmi.eybond.com/hmi/api/hmi/domain/check/shineOrDess/auth/checkDomainPass/1?domainName=shinemonitor.com`
2. `GET https://aam.eybond.com/ws/?action=queryDomainApp&i18n=en_US&domain=shinemonitor.com`
3. `GET https://web.shinemonitor.com/public/?action=queryDomainListNotLogin&source=1&_app_client_=web&_app_id_=shinemonitor.com&_app_version_=1.0.6.3&i18n=en_US` (also signed, with `salt` but no token)

For an HA integration these can be **skipped**; the only thing they produce that we need is `company-key`, which is a stable per-domain constant (for `shinemonitor.com` it's `bnrl_frRFjEz8Mkn`).

### 3.2 Login request

```
GET /public/?sign=<…>&salt=<ms>
          &action=auth
          &usr=<username>
          &company-key=bnrl_frRFjEz8Mkn
```

Signing inputs for `sign`:

```
sha1( salt + sha1(password_utf8) + "&action=auth&usr=<usr>&company-key=bnrl_frRFjEz8Mkn" )
```

### 3.3 Login response schema

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "secret": "<40-hex-chars>",
    "token":  "<64-hex-chars>",
    "expire": 432000,
    "uid":    &lt;your-uid&gt;,
    "usr":    "&lt;your-username&gt;",
    "role":   0
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `secret` | string (hex, 40) | Per-session signing secret. Keep private. |
| `token` | string (hex, 64) | Per-session bearer token. Sent with every subsequent call. |
| `expire` | int (seconds) | `432000` = **5 days**. Re-auth before this elapses. |
| `uid` | int | User id — used by some endpoints. |
| `role` | int | `0` = plant owner/proprietor. |

Real captured values for this investigation:

```json
{
  "pwdSha1": "&lt;sha1-of-password&gt;",
  "secret":  "&lt;secret-40-hex&gt;",
  "token":   "&lt;token-64-hex&gt;",
  "uid":     &lt;your-uid&gt;,
  "expire":  432000
}
```

### 3.4 Session persistence on the web client

The browser stores state entirely in `localStorage`:

```json
{
  "globalStoragePwd": "{\"pwd\":\"…\",\"username\":\"…\",\"isChecked\":false}",
  "globalStorage":    "{\"accounts\":[{\"usr\":\"…\",\"pwdSha1\":\"…\",\"gts\":<ms>,\"dat\":{…login response…},\"lang\":1}]}"
}
```

`gts` is the login wall-clock in ms. Re-auth when `Date.now() - gts >= expire * 1000`.

### 3.5 Token-refresh / re-auth policy

- On any response whose `desc` implies auth failure (e.g. `ERR_NO_PERMISSION`, `ERR_TOKEN_EXPIRED`), discard the cached `token`/`secret` and re-run §3.2.
- Proactively refresh 10 min before `expire` to avoid mid-poll failures.

---

## 4. Endpoint catalog

All endpoints below are reached via `GET /public/?…&action=<X>&…`. The table groups them by the dashboard screen that calls them.

### 4.1 Plant & user

| Action | Required params | Purpose |
|---|---|---|
| `queryPlantsInfo` | – | List plants owned by the logged-in user. |
| `queryPlantInfo` | `plantid` | Full plant profile (location, nominal power, images). |
| `queryPlantDeviceStatus` | `plantid` | Online/offline state of collectors and devices. |
| `queryCollectors` | `plantid`, `pn`, `page`, `pagesize` | Datalogger hardware details. |
| `queryPlantCamera` | `plantid` | Cameras bound to the plant (empty for most). |
| `queryPlantElectricmeter` | `pid` | Meter binding (if any). |

### 4.2 Alarms

| Action | Required params | Purpose |
|---|---|---|
| `webQueryPlantsWarning` | `sdate`, `edate`, `handle=false`, optional `plantid`/`pn`/`page`/`pagesize` | Active/past alarm events. |

### 4.3 Plant energy & power (Overview screen)

| Action | Required params | Purpose |
|---|---|---|
| `queryPlantActiveOuputPowerOneDay` | `plantid`, `date=YYYY-MM-DD` | Today's power curve (W vs time). |
| `queryTodayDevicePvCharts` | `plantid`, `pns`, `devcodes`, `sns`, `devaddrs` | Today's PV production per string. |
| `queryPlantEnergyMonthPerDay` | `plantid`, `date=YYYY-MM` | Daily kWh for one month. |
| `queryPlantEnergyYearPerMonth` | `plantid`, `date=YYYY` | Monthly kWh for one year. |
| `queryPlantEnergyTotalPerYear` | `plantid` | Yearly kWh lifetime totals. |
| `queryPlantDeviceDesignatedInformation` | `plantid`, `devtype`, `parameter=energy_today,energy_total` | **Best single call for dashboard tiles** (combined today + lifetime per device). |

### 4.4 Device schema & realtime (Device Management screen)

Device identity is the 4-tuple `(pn, devcode, devaddr, sn)`.

| Action | Required params | Purpose |
|---|---|---|
| `queryPlantDeviceChartsFields` | `plantid`, `devtype` | Fields available on a device type (basic). |
| `queryPlantDeviceChartsFieldsByType` | `plantid`, `devtype`, `devcode`, `type=1` | **Full** field list with names + units. Use this to auto-build sensors. |
| `queryDeviceRealLastData` | `pn`, `devcode`, `sn`, `devaddr`, `date` | Latest snapshot of all fields. |
| `queryDeviceActiveOuputPowerOneDay24Hour` | `pn`, `devcode`, `sn`, `devaddr`, `date` | 24-h active-power curve for today. |
| `queryDeviceDataOneDayPaging` | `pn`, `devcode`, `sn`, `devaddr`, `date`, `page`, `pagesize` | Raw tabular readings table. |
| `queryDeviceChartFieldDetailData` | `pn`, `devcode`, `sn`, `devaddr`, `field`, `precision`, `sdate`, `edate` | Time-series for one field over any range. `field` = `optional` id from the schema call. |

---

## 5. Endpoint reference — request / response detail

Conventions:
- Path is always `GET /public/?…`. The table lists only the **action-specific** params (on top of the always-present `sign`, `salt`, `token`, `i18n=en_US`, `lang=en_US`).
- Each example omits `sign`/`salt`/`token` for readability.

### 5.1 `auth`

**Request params**

| Name | Type | Example | Notes |
|---|---|---|---|
| `action` | string | `auth` | |
| `usr` | string | `&lt;your-username&gt;` | Username |
| `company-key` | string | `bnrl_frRFjEz8Mkn` | Domain constant |

**Response (200)**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "secret": "&lt;secret-40-hex&gt;",
    "expire": 432000,
    "token":  "&lt;token-64-hex&gt;",
    "role":   0,
    "usr":    "&lt;your-username&gt;",
    "uid":    &lt;your-uid&gt;
  }
}
```

### 5.2 `queryPlantsInfo`

**Request params**: none beyond the defaults.

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "total": 0,
    "page": 0,
    "pagesize": 0,
    "info": [
      { "uid": &lt;your-uid&gt;, "usr": "&lt;your-username&gt;", "pid": &lt;your-plantid&gt;, "pname": "Home", "status": 1 }
    ]
  }
}
```

`status`: `1` = normal, other values represent offline/warning/error plant states.

### 5.3 `queryPlantInfo`

**Request params**

| Name | Type | Example |
|---|---|---|
| `plantid` | int | `&lt;your-plantid&gt;` |
| `date` (optional) | string `YYYY-MM-DD HH:MM:SS` | `2026-04-17 02:13:27` |

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "pid": &lt;your-plantid&gt;,
    "uid": &lt;your-uid&gt;,
    "name": "Home",
    "status": 1,
    "energyOffset": 0.0,
    "address": {
      "country": "India", "province": "Rajasthan", "city": "Sri Ganganagar",
      "address": "Old Abadi Ganga Nagar",
      "lon": "73.854018", "lat": "29.931562",
      "timezone": 18000
    },
    "profit": {
      "unitProfit": "1.2000",
      "currency":   "₹",
      "coal":       "0.400",
      "co2":        "0.990",
      "so2":        "0.030",
      "soldProfit": 0.0, "selfProfit": 0.0,
      "purchProfit":0.0, "consProfit":0.0, "feedProfit":0.0
    },
    "nominalPower":       "5.0000",
    "energyYearEstimate": "0.0000",
    "picBig":   "https://img.shinemonitor.com/img/2024/04/02/…838.jpg",
    "picSmall": "https://img.shinemonitor.com/img/2024/04/02/…839.jpg",
    "install": "2025-01-20 10:54:59",
    "gts":     "2025-01-20 10:25:03",
    "flag":    true
  }
}
```

`timezone` is seconds offset from UTC (18000 = +05:30 for India). `nominalPower` is in kW. `profit.co2` etc. are per-kWh coefficients (kg) used by the "environmental impact" tiles.

### 5.4 `queryPlantDeviceStatus`

**Request params**: `plantid`, optional `pn=`.

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "status": 1,
    "collector": [
      {
        "pn":    "&lt;datalogger-PN&gt;",
        "alias": "My Devices5113",
        "status": 1,
        "device": [
          { "devcode": 632, "devaddr": 1, "sn": "&lt;inverter-SN&gt;", "status": 1, "comStatus": 0 }
        ]
      }
    ]
  }
}
```

| Field | Meaning |
|---|---|
| `collector[].status` | Collector alarm flag. `0` = normal, `1` = alarm (e.g. offline too long). |
| `device[].status` | Device alarm flag. `0` = normal, `1` = alarm. **Not an online flag** — a live inverter that's producing reports `status=0`. |
| `device[].comStatus` | Data freshness. `1` = cloud is receiving fresh readings, `0` = stale (offline/asleep). **This is the real "is online" signal** used by the shinemonitor.com web UI. |

### 5.5 `queryCollectors`

**Request params**: `plantid`, `pn` (may be empty), `page`, `pagesize`.

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "total": 1, "page": 0, "pagesize": 20,
    "collector": [
      {
        "pn":                "&lt;datalogger-PN&gt;",
        "alias":             "My Devices5113",
        "datFetch":          300,
        "timezone":          18000,
        "load":              1,
        "status":            1,
        "type":              0,
        "uid":               &lt;your-uid&gt;,
        "pid":               &lt;your-plantid&gt;,
        "fireware":          "7.63.6.191",
        "collectorPicture":  "http://oss.sz.eybond.com/CRM/2024-08-07/1723019653377-a63c2540.png"
      }
    ]
  }
}
```

`datFetch` is the datalogger's upload interval in seconds. **Your client must not poll faster than this** (300 s for this unit) — you'll get stale values or rate-limited.

### 5.6 `queryPlantDeviceDesignatedInformation`

Single best call for dashboard cards — avoids the offline-only `queryDeviceRealLastData`.

**Request params**: `plantid`, `devtype` (e.g. `512` for grid-tie inverter), `parameter=energy_today,energy_total` (comma-separated list of field ids).

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "device": [
      {
        "devcode": 632,
        "sn":      "&lt;inverter-SN&gt;",
        "devaddr": 1,
        "status":  1,
        "cpn":     "&lt;datalogger-PN&gt;",
        "vendor":  "",
        "ratePower": "0",
        "devtype": "",
        "energy_today": "29.0000",
        "energy_total": "10814.0000",
        "lts":     "2026-04-16 18:34:49",
        "alias":   "&lt;inverter-SN&gt;"
      }
    ]
  }
}
```

`lts` = "last timestamp" the cloud received from the device. Use it as the `last_reported` HA entity attribute.

### 5.7 `queryPlantEnergyMonthPerDay`

**Request params**: `plantid`, `date=YYYY-MM`.

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "perday": [
      { "val": "23.0000", "ts": "2026-04-01 00:00:00" },
      { "val": "22.0000", "ts": "2026-04-02 00:00:00" },
      …
      { "val":  "0.0000", "ts": "2026-04-30 00:00:00" }
    ],
    "energyTotal": 0.0
  }
}
```

Missing days are returned as `"0.0000"` (not null). Timestamps are local to the plant's timezone.

### 5.8 `queryPlantEnergyYearPerMonth`

**Request params**: `plantid`, `date=YYYY`.

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "permonth": [
      { "val": "458.0000", "ts": "2026-01-01 00:00:00" },
      { "val": "550.0000", "ts": "2026-02-01 00:00:00" },
      { "val": "747.0000", "ts": "2026-03-01 00:00:00" },
      { "val": "443.0000", "ts": "2026-04-01 00:00:00" },
      …
      { "val":   "0.0000", "ts": "2026-12-01 00:00:00" }
    ]
  }
}
```

### 5.9 `queryPlantEnergyTotalPerYear`

**Request params**: `plantid`.

**Response**

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": {
    "peryear": [
      { "val": "8087.0000", "ts": "2025-01-01 00:00:00" },
      { "val": "2198.0000", "ts": "2026-01-01 00:00:00" }
    ]
  }
}
```

### 5.10 `queryPlantActiveOuputPowerOneDay`

**Request params**: `plantid`, `date=YYYY-MM-DD`.

**Response (populated)**: `dat` is an array of `{val, ts}` at the collector's upload cadence (5-min samples).
**Response (empty)**: `{"err":12,"desc":"ERR_NO_RECORD"}` — happens when the day hasn't produced data yet.

### 5.11 `queryTodayDevicePvCharts`

**Request params**: `plantid`, and parallel arrays `pns`, `devcodes`, `sns`, `devaddrs` (comma-separated; duplicate the device as many times as strings you want to chart).

**Response**

```json
{ "err": 0, "desc": "ERR_NONE", "dat": [ { "key": "&lt;inverter-SN&gt;", "val": "0" } ] }
```

`val` is current/today PV energy in kWh for each series.

### 5.12 `queryPlantDeviceChartsFieldsByType` (field schema)

**Request params**: `plantid`, `devtype` (e.g. `512`), `devcode` (e.g. `632`), `type=1`.

**Response** (truncated)

```json
{
  "err": 0,
  "desc": "ERR_NONE",
  "dat": [
    { "optional": "eybond_read_28", "name": "PV1 voltage",      "uint": "V",   "order": 1000 },
    { "optional": "eybond_read_29", "name": "PV1 current",      "uint": "A",   "order": 1000 },
    { "optional": "output_power",   "name": "Output Power",     "uint": "W",   "order": 1000 },
    { "optional": "energy_today",   "name": "Energy today",     "uint": "kWh", "order": 1000 },
    { "optional": "energy_total",   "name": "energy_total",     "uint": "kWh", "order": 1000 },
    { "optional": "eybond_read_43", "name": "Grid frequency",   "uint": "HZ",  "order": 1000 },
    { "optional": "eybond_read_49", "name": "Internal ambient temperature", "uint": "°C", "order": 1000 },
    { "optional": "eybond_read_50", "name": "Internal radiator temperature","uint": "°C", "order": 1000 }
  ]
}
```

| Field | Meaning |
|---|---|
| `optional` | Field id — pass this as `field=` to `queryDeviceChartFieldDetailData`. Use it as the HA sensor key. |
| `name` | Human label. |
| `uint` | Unit (note the typo — it's "unit"). |
| `order` | Display order (ignore for HA). |

Full list captured for `devcode=632` (likely Kstar / Sofar-rebrand grid-tie):

| Field | Name | Unit |
|---|---|---|
| `eybond_read_28`..`33` | PV1/PV2/PV3 voltage & current | V / A |
| `eybond_read_34`..`39` | Grid R/S/T voltage & current | V / A |
| `eybond_read_40`..`42` | Grid line voltage RS/ST/TR | V |
| `eybond_read_43` | Grid frequency | Hz |
| `eybond_read_44` | Bus voltage | V |
| `eybond_read_49` | Internal ambient temperature | °C |
| `eybond_read_50` | Internal radiator temperature | °C |
| `eybond_read_16` | Output S (apparent power) | VA |
| `eybond_read_17` | Output Q (reactive power) | VA |
| `eybond_read_20` | Instrument power | W |
| `eybond_read_23` | Cumulative operating time | h |
| `eybond_read_25` | Waiting time | s |
| `output_power` | Output power | W |
| `energy_today` | Energy today | kWh |
| `energy_total` | Energy lifetime | kWh |

### 5.13 `queryDeviceRealLastData`

**Request params**: `pn`, `devcode`, `sn`, `devaddr`, `date=YYYY-MM-DD`.

**Response**: an object whose keys match the `optional` ids from §5.12, each with a current `val` and `ts`. Returns `ERR_NO_RECORD` if the device is offline (`comStatus=0`). The user's inverter was offline at capture time, so a populated example could not be recorded — use the field schema above as the contract.

### 5.14 `queryDeviceActiveOuputPowerOneDay24Hour`

**Request params**: `pn`, `devcode`, `sn`, `devaddr`, `date=YYYY-MM-DD`.

**Response**: `dat` = list of `{val, ts}` at the device's upload cadence.

### 5.15 `queryDeviceDataOneDayPaging`

**Request params**: `pn`, `devcode`, `sn`, `devaddr`, `date=YYYY-MM-DD`, `page`, `pagesize`, optional `oddEvenRow=null`.

**Response**: paged tabular dump of every field reading for the day — used by the "Export" button. Schema mirrors §5.12 expanded across rows.

### 5.16 `queryDeviceChartFieldDetailData`

**Request params**: `pn`, `devcode`, `sn`, `devaddr`, `field=<optional>`, `precision`, `sdate=YYYY-MM-DD HH:MM:SS`, `edate=YYYY-MM-DD HH:MM:SS`.

**Response**: time-series for the requested field over the range. `precision` controls down-sampling (observed value: `5`).

### 5.17 `webQueryPlantsWarning`

**Request params** (one of two shapes used by the UI):

- Plant-list view: `sdate`, `edate`, `handle=false`, optional `page`, `pagesize`.
- Plant-detail view: `plantid`, `handle=false`, `mode=strict`, `pn=`, `page`, `pagesize`.

**Response (empty)**

```json
{ "err": 264, "desc": "ERR_NOT_FOUND_DEVICE_WARNING" }
```

**Response (populated)**: `dat` is a paginated list of alarm objects with device identifiers, severity, first-occurrence and clear timestamps, description, and a handle/processing flag.

---

## 6. Captured constants for this account

| Key | Value |
|---|---|
| Domain company key | `bnrl_frRFjEz8Mkn` |
| Plant id | `&lt;your-plantid&gt;` |
| Plant name | `Home` |
| User id | `&lt;your-uid&gt;` |
| Username | `&lt;your-username&gt;` |
| Datalogger PN | `&lt;datalogger-PN&gt;` |
| Datalogger firmware | `7.63.6.191` |
| Datalogger upload interval | `300 s` |
| Inverter devtype | `512` (grid-tie) |
| Inverter devcode | `632` |
| Inverter SN | `&lt;inverter-SN&gt;` |
| Inverter devaddr | `1` |
| Nominal power | `5.0 kW` |
| Plant timezone | `UTC+05:30` (`18000`) |
| Location | `29.931562, 73.854018` (Sri Ganganagar, Rajasthan, India) |

---

## 7. Polling plan for the Home Assistant integration

Single `DataUpdateCoordinator` per plant, `SCAN_INTERVAL = 300 s` (matches `datFetch`). Each tick:

1. `queryPlantDeviceStatus(plantid)` → device-level `binary_sensor.*_online`.
2. `queryPlantDeviceDesignatedInformation(plantid, devtype=512, parameter=energy_today,energy_total)` → dashboard cards (`energy_today`, `energy_total`, `lts`).
3. For each online device, `queryDeviceRealLastData(pn, devcode, sn, devaddr, today)` → instantaneous sensors (V/I/P/°C).
4. `webQueryPlantsWarning(plantid, handle=false, pn, page=0, pagesize=10)` → alarm diagnostics.

One-off at integration setup:
- `queryPlantsInfo` → discover `plantid`s.
- `queryPlantInfo(plantid)` → device-info card (name, location, nominal power, currency, CO₂ coefficient).
- `queryCollectors(plantid)` → confirm `datFetch`.
- `queryPlantDeviceChartsFieldsByType(plantid, devtype, devcode, type=1)` → auto-build the sensor list.

Long-term statistics feed (for the Energy Dashboard):
- `queryPlantEnergyMonthPerDay(plantid, YYYY-MM)` hourly backfill on the first run, then only today's delta per tick.
- `queryPlantEnergyYearPerMonth`, `queryPlantEnergyTotalPerYear` for historical baselines.

---

## 8. Operational caveats

1. **Poll cadence.** Never faster than `datFetch`. Otherwise many endpoints reply `ERR_NO_RECORD` for half the tick.
2. **Offline periods.** At night or on connectivity loss, `comStatus=0` and all `queryDevice*` realtime calls return `ERR_NO_RECORD`. Treat that as "unavailable" in HA, not an error.
3. **Clock skew.** `salt` is the client's `Date.now()`. The server doesn't seem to reject skewed salts aggressively, but drift beyond a few minutes has been reported to intermittently fail. Keep host NTP-synced.
4. **Rate limiting.** Not documented. Empirically the web UI fires ~15 XHRs on login without issue. Stay within tens of requests per minute per user.
5. **Re-auth.** Cache `{token, secret, expire, gts}` to disk so restarts don't re-login every time. Refresh 10 minutes before expiry.
6. **Parameter order.** The signed string and the URL must agree byte-for-byte. If you use a helper that sorts params alphabetically, disable it.
7. **Character encoding.** Observed encoding leaves `,` and `:` unencoded and uses `%20` for spaces. Reproduce that exactly.
8. **`i18n` / `lang` duplication.** The UI sometimes sends `i18n=en_US` twice in the URL. The server accepts it. Single occurrence is fine.

---

## 9. Security notes

- `secret` + `token` together are equivalent to a session — treat them like a password.
- `pwdSha1` alone is sufficient to sign auth (it's the effective credential). **SHA-1 of the password, once leaked, is as good as the password** for this service. Store securely.
- `company-key` is public (shipped in bootstrap).
- There is no TOTP/2FA. Anyone with `usr` + `password` can sign in.
- All traffic is HTTPS. The domain uses an nginx/1.9.6 server banner — consistent with an Eybond-managed stack.

---

## 10. Open questions / future investigation

1. **Battery/hybrid devices** (`devtype != 512`) expose different field sets. If the user adds a hybrid inverter, `queryPlantDeviceChartsFieldsByType` must be re-run for that `devcode`.
2. **Control endpoints** (setting parameters, silencing alarms) exist in the UI ("Field control" button under Data Details) but were not exercised in this investigation.
3. **WebSocket / push streams.** None observed — the UI is poll-only.
4. **Multi-plant accounts.** Only one plant in the captured session; the `queryPlants*` endpoints are paginated so should scale cleanly.
5. **Live inverter payload.** The device was offline during capture; `queryDeviceRealLastData` returned `ERR_NO_RECORD`. A follow-up capture while the plant is producing will confirm the full realtime field shape.

---

## Appendix A — Captured session transcript (abridged)

Chronological XHR log from a single "login → browse all screens" session. `ms` = epoch-ms `salt` (subtract to get relative offsets).

| salt (ms) | action | key params |
|---|---|---|
| 1776372049429 | queryDomainListNotLogin | bootstrap |
| 1776372088553 | **auth** | usr=&lt;your-username&gt; |
| 1776372097637 | queryPlantsInfo | – |
| 1776372097638 | webQueryPlantsWarning | global |
| 1776372097639 | webQueryPlantsWarning | paginated |
| 1776372097857 | queryPlantInfo | plantid=&lt;your-plantid&gt; |
| 1776372098810 | queryPlantCamera | plantid |
| 1776372098811 | queryPlantElectricmeter | pid |
| 1776372100774 | queryPlantDeviceStatus | plantid |
| 1776372101344 | queryTodayDevicePvCharts | plantid + device arrays |
| 1776372101345 | queryPlantActiveOuputPowerOneDay | plantid, date |
| 1776372164468 | queryPlantEnergyMonthPerDay | date=2026-04 |
| 1776372172467 | queryPlantEnergyYearPerMonth | date=2026 |
| 1776372176852 | queryPlantEnergyTotalPerYear | plantid |
| 1776372180526 | queryPlantDeviceStatus | plantid (refresh) |
| 1776372182676 | queryCollectors | page=0, pagesize=20 |
| 1776372184810 | queryDeviceRealLastData | device 4-tuple, today |
| 1776372185854 | queryDeviceActiveOuputPowerOneDay24Hour | device, today |
| 1776372191533 | queryDeviceDataOneDayPaging | device, page=0, pagesize=50 |
| 1776372201547 | webQueryPlantsWarning | plantid, page=0, pagesize=10 |
| 1776372207782 | queryPlantInfo | with timestamp |
| 1776372211723 | queryPlantDeviceChartsFields | devtype=512 |
| 1776372211724 | queryPlantDeviceDesignatedInformation | parameter=energy_today,energy_total |
| 1776372212082 | queryPlantDeviceChartsFieldsByType | devcode=632, type=1 |
| 1776372212650 | queryDeviceChartFieldDetailData | field=output_power |

---

## Appendix B — Minimal Python client

```python
import hashlib, time, urllib.parse, httpx, json

BASE = "https://web.shinemonitor.com/public/"
COMPANY_KEY = "bnrl_frRFjEz8Mkn"

class ShineClient:
    def __init__(self, usr: str, pwd: str):
        self.usr = usr
        self.pwd_sha1 = hashlib.sha1(pwd.encode()).hexdigest()
        self.token: str | None = None
        self.secret: str | None = None
        self.expire_at: float = 0.0

    @staticmethod
    def _sha1(s: str) -> str:
        return hashlib.sha1(s.encode()).hexdigest()

    def _build(self, params: list[tuple[str, str]]) -> str:
        salt = str(int(time.time() * 1000))
        suffix = "&" + urllib.parse.urlencode(params, safe=",: ")
        if self.token is None:          # auth
            sign = self._sha1(salt + self.pwd_sha1 + suffix)
            return f"{BASE}?sign={sign}&salt={salt}{suffix}"
        sign = self._sha1(salt + self.secret + self.token + suffix)
        return f"{BASE}?sign={sign}&salt={salt}&token={self.token}{suffix}"

    async def _call(self, client: httpx.AsyncClient, action: str, **params):
        params = [("action", action), *params.items(),
                  ("i18n", "en_US"), ("lang", "en_US")]
        r = await client.get(self._build(params))
        r.raise_for_status()
        return r.json()

    async def login(self, client: httpx.AsyncClient):
        params = [("action", "auth"),
                  ("usr", self.usr),
                  ("company-key", COMPANY_KEY)]
        url = self._build(params)          # no token branch
        data = (await client.get(url)).json()["dat"]
        self.token, self.secret = data["token"], data["secret"]
        self.expire_at = time.time() + data["expire"] - 600

    async def ensure_auth(self, client):
        if self.token is None or time.time() > self.expire_at:
            await self.login(client)

    async def plants(self, c):      return (await self._call(c, "queryPlantsInfo"))["dat"]["info"]
    async def plant(self, c, pid):  return (await self._call(c, "queryPlantInfo", plantid=pid))["dat"]
    async def status(self, c, pid): return (await self._call(c, "queryPlantDeviceStatus", plantid=pid))["dat"]
    async def realtime(self, c, pn, devcode, sn, devaddr, date):
        return (await self._call(c, "queryDeviceRealLastData",
                pn=pn, devcode=devcode, sn=sn, devaddr=devaddr, date=date))["dat"]
    async def energy_today_total(self, c, pid, devtype=512):
        return (await self._call(c, "queryPlantDeviceDesignatedInformation",
                plantid=pid, devtype=devtype, parameter="energy_today,energy_total"))["dat"]
```
