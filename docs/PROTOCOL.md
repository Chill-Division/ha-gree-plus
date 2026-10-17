# Gree+ cloud protocol notes

These notes describe how current-generation Gree air conditioners managed by the **Gree+** app
communicate, as observed on real units. They are written so that anyone else building on these
devices doesn't have to start from scratch. Everything here was verified against live hardware
unless marked *unverified*.

Reference hardware:

| Field | Value |
| --- | --- |
| App | GREE+ (Android `com.gree.greeplus`) |
| Model ID (`mid`) | `10001` |
| Model version shown in app | V20.8 |
| WiFi module firmware (`hid`) | `362001066300+U-4MWB05DF13V3.10.bin` (app shows V3.10) |
| Protocol version (`ver`) | `V3.2.M` |
| `ModelType` | `21200912` |
| Region chosen in app | New Zealand |

All identifiers below (MACs, user ids, keys, tokens) are placeholders.

## Contents

1. [Why the older integrations don't work](#why-the-older-integrations-dont-work)
2. [Architecture](#architecture)
3. [Cloud REST API](#cloud-rest-api)
4. [MQTT broker](#mqtt-broker)
5. [Message envelope and encryption](#message-envelope-and-encryption)
6. [Requests and replies](#requests-and-replies)
7. [Re-pairing a unit](#re-pairing-a-unit)
8. [Properties](#properties)
9. [Device-side connection](#device-side-connection)
10. [Open questions](#open-questions)

## Why the older integrations don't work

Older Gree WiFi modules expose a LAN protocol on UDP port 7000 (`{"t":"scan"}`, `bind`,
`status`, `cmd`), which is what Home Assistant's built-in `gree` integration and most custom
components use. The units described here **do not answer on the LAN at all**:

* no reply to `{"t":"scan"}` on UDP 7000, either unicast or broadcast;
* no open TCP ports (1–1024 plus the usual MQTT/HTTP alternates were checked).

The Gree+ app itself still broadcasts a 12-byte `{"t":"scan"}` to `255.255.255.255:7000` when
it opens; these units simply ignore it. All control goes through Gree's cloud, as described below.

## Architecture

```
 Gree+ app / this integration                Gree cloud                      AC WiFi module
 ───────────────────────────                 ──────────                      ──────────────
  HTTPS  ──────────────────────────────▶  <region>grih.gree.com
         login, homes, devices + keys       (REST API)

  MQTT/TLS :1984  ─────────────────────▶  mqtt-<region>.gree.com  ◀──────  MQTT/TLS :1982
         publish  request/<mac>             (same broker host,               subscribes to requests,
         subscribe response/<mac>,           different ports)                publishes replies/status
                   status/<mac>,
                   connect/<mac>
```

The app and the unit never talk directly. The broker relays the app's `request/<mac>` messages to
the unit, and the unit's replies come back on `response/<mac>`.

## Cloud REST API

The REST API was first documented by [luc10/gree-api-client](https://github.com/luc10/gree-api-client).

### Servers

| Region | REST API | MQTT broker |
| --- | --- | --- |
| Australia (**also New Zealand**) | `https://augrih.gree.com` | `mqtt-au.gree.com` |
| Europe | `https://eugrih.gree.com` | `mqtt-eu.gree.com` |
| North America | `https://nagrih.gree.com` | `mqtt-na.gree.com` |
| South America | `https://sagrih.gree.com` | `mqtt-sa.gree.com` |
| Latin America | `https://lagrih.gree.com` | `mqtt-la.gree.com` |
| East / South-East Asia | `https://hkgrih.gree.com` | `mqtt-as.gree.com` |
| India | `https://ingrih.gree.com` | `mqtt-in.gree.com` |
| Middle East | `https://megrih.gree.com` | `mqtt-me.gree.com` |
| Russia | `https://rugrih.gree.com` | `mqtt-ru.gree.com` |
| China mainland | `https://grih.gree.com` | `mqtt.gree.com` |

An account exists on exactly one server. Logging in to any other server returns
`{"r": 411, "msg": "The password is incorrect or the user does not exist."}`, so trying each
server in turn is a reliable way to auto-detect the region. Only the Australian rows have been
verified first-hand.

### Request format

Every call is an HTTP `POST` with these headers:

```
Content-Type: application/x-www-form-urlencoded
Gaen1: 5ac2bdf935bcca70
Charset: utf-8
```

The body is `base64(AES-128-ECB(PKCS#7(json)))`, keyed with `#G$&^jgfujy6ujxt`. The JSON is
the endpoint's payload plus two signature fields:

```jsonc
{
  "api": {
    "appId": "4920681951525131286",
    "r": 1792195200,                 // unix time (UTC)
    "t": "2026-10-17 00:00:00",      // same instant, UTC, "%Y-%m-%d %H:%M:%S"
    "vc": "md5(appId_appHash_t_r)"   // appHash = 0fa513124aa97781d1f3f40d61ca1a89
  },
  "datVc": "md5(appHash_<signed field values joined by _>)",
  ...payload
}
```

The response is `{"enRes": "<base64 AES-ECB, same key>"}`. Once decrypted it contains `r`
(200 on success) and the result fields.

### Endpoints

| Endpoint | Payload | Signed fields | Returns |
| --- | --- | --- | --- |
| `/App/UserLoginV2` | `user`, `psw`, `t` | `user`, `psw`, `t` | `uid`, `token` (+ profile) |
| `/App/GetHomes` | `token`, `uid` | `token`, `uid` | `home[]` (`id`, `name`) |
| `/App/GetDevsInRoomsOfHomeV2` | `token`, `uid`, `homeId` | `token`, `uid`, `homeId` | `rooms[].devs[]` |

* `psw` = `md5(md5(md5(password) + password) + t)`, where `t` is the same UTC timestamp string.
* Accounts created with Google sign-in (`utype: "GG"`) still log in with email and password here.
  The password is the one set in the Gree+ app.
* Logging in again does **not** invalidate other sessions, including the phone app's.

Each entry in `devs[]` includes:

```jsonc
{
  "mac": "aabbccddeeff",   // 12 hex chars, no separators
  "name": "AC1234",        // name shown in the app
  "key": "0123456789abcdef", // 16-char AES key for this unit's MQTT packs
  "mid": "10001",          // model id
  "ver": "V3.2.M",
  "vender": "1",
  "ssid": "...", "bindTime": "...", "pmac": "", "subdivCode": "", ...
}
```

## MQTT broker

| | |
| --- | --- |
| Host | `mqtt-<region>.gree.com` (see table above) |
| Port | **1984**, TLS. The certificate is a public GlobalSign `*.gree.com` one, so normal verification works. |
| Protocol | MQTT 3.1.1, clean session |
| Username / password | `uid` / `token` from `UserLoginV2` |
| Client id | anything; the app uses `app_<16 hex>` |

The broker enforces per-account ACLs. With an account's credentials you may subscribe to these
topics, for units on that account only:

| Topic | Direction | Content |
| --- | --- | --- |
| `request/<mac>` | app → unit | commands and status queries (subscribing shows **other** clients' requests too, e.g. the phone app's) |
| `response/<mac>` | unit → app | replies; envelope `tcid` = the requester's `cid` |
| `status/<mac>` | unit → all | **retained** full snapshot (145 properties), pushed about 5 s after any change |
| `connect/<mac>` | broker → all | **retained** online/offline notice |
| `event/<mac>/#` | unit → all | subscription allowed; nothing observed yet |

Wildcards outside these (e.g. `#`, `status/#`) are refused with SUBACK failure.

## Message envelope and encryption

Every message on every topic is a JSON envelope:

```json
{"cid": "4293351429", "i": 0, "pack": "<base64>", "t": "pack", "tcid": "aabbccddeeff", "uid": 1234567}
```

| Field | Request (app → unit) | Reply (unit → app) |
| --- | --- | --- |
| `cid` | random 10-digit request id | the unit's MAC |
| `tcid` | the unit's MAC | the request's `cid` (use it to match replies) |
| `uid` | account uid | account uid (`0` on `status/`) |
| `i` | `0` | `0` (`1` on `connect/`) |
| `pack` | encrypted payload | encrypted payload |

`pack` is `base64(AES-128-ECB(PKCS#7(json)))`:

* `request/`, `response/` and `status/` use the unit's own `key` from the REST API.
* `connect/` uses Gree's generic key `a3K8Bx%2r8Y7#xDh`.

AES-GCM ("cipher v2"), which some newer local-protocol modules use, was **not** used by these units.

## Requests and replies

### Status query

```json
{"t": "status", "mac": "aabbccddeeff", "cols": ["Pow", "Mod", "SetTem", "WdSpd"]}
```

Reply on `response/<mac>` (typically under 200 ms):

```json
{"t": "dat", "mac": "aabbccddeeff", "cid": "aabbccddeeff", "uid": 1234567, "r": 200,
 "cols": ["Pow", "Mod", "SetTem", "WdSpd"], "dat": [1, 0, 19, 0]}
```

### Command

```json
{"t": "cmd", "mac": "aabbccddeeff", "opt": ["SetTem", "TemUn"], "p": [19, 0]}
```

Several properties can be set in one command. The acknowledgement echoes them back:

```json
{"t": "res", "mac": "aabbccddeeff", "opt": ["SetTem", "TemUn"], "p": [19, 0], "r": 200}
```

About 5 seconds after a command, the unit also publishes a fresh full snapshot to `status/<mac>`
(retained), so other clients learn about the change.

### What the Gree+ app sends

Captured from the app (by subscribing to `request/<mac>`) while each control was used.
`ReportClientID` is an id the app adds to every command; the unit drops it from the ack.

| App control | `opt` → `p` |
| --- | --- |
| Fan: Auto | `WdSpd, Quiet, Tur` → `0, 0, 0` |
| Fan: Low … High | `WdSpd, Quiet, Tur` → `1…5, 0, 0` |
| Fan: Quiet | `Quiet, Tur` → `2, 0` |
| Up-down swing | `SwUpDn` → `1` |
| Up-down fixed angles | `SwUpDn` → `2, 3, 4, 5, 6` (top → bottom) |
| Left-right swing | `SwingLfRig` → `1` |
| Left-right fixed angles | `SwingLfRig` → `2, 3, 4, 5, 6` (left → right) |
| Swing off | `SwUpDn` → `0`, `SwingLfRig` → `0` |

When a unit's page is opened, the app also sends

```json
{"t": "freqCount", "mac": "aabbccddeeff", "opt": ["SendFreq", "FreqTime"], "p": [1, 300]}
```

which appears to ask the unit to report state changes promptly for the next 300 seconds,
and it queries firmware versions with `{"t": "status", "cols": ["hid", "InHid", "OEEPHid"]}`.

### Pushed status (`status/<mac>`)

```json
{"t": "dat", "r": 200, "mac": "aabbccddeeff", "sub": "", "cols": [...145 names...], "dat": [...]}
```

Because it is retained, a new subscriber immediately receives the **last** snapshot, which can be
weeks old if nothing has changed since. Treat retained snapshots as stale and query instead.

### Online notice (`connect/<mac>`, generic key)

```json
{"mac": "aabbccddeeff", "online": 1, "t": "connect", "onlineSub": [], "offlineSub": [], "ts": 1792193413488, "sleep": 0}
```

## Re-pairing a unit

Observed when a unit was removed from the account and paired again:

* The unit gets a **new device key**. Clients must fetch keys again from
  `GetDevsInRoomsOfHomeV2`; the old key no longer decrypts its messages.
* The unit came back **powered off** (`Pow=0`) and with `Health` cleared, probably because the
  WiFi reset on the remote is done with the unit off. Check its settings afterwards.
* Right after binding, the cloud sends the unit a time message on `response/<mac>`
  (not encrypted with the device key):
  `{"t": "tm", "time": "<UTC>", "timestamp": ..., "encrypt": "<base64>", "homeId": 0, "reportAnyway": false}`.
* The unit kept its old broker session open for about a minute after re-pairing before resetting it.

## Properties

These are the properties the integration uses. Values are integers unless noted.

| Property | Meaning | Values |
| --- | --- | --- |
| `Pow` | power | 0 off, 1 on |
| `Mod` | mode | 0 auto, 1 cool, 2 dry, 3 fan, 4 heat |
| `SetTem` | target temperature | °C, **no offset** (16–30) |
| `TemUn` | display unit | 0 °C, 1 °F |
| `WdSpd` | fan speed | 0 auto, 1 low, 2 medium-low, 3 medium, 4 medium-high, 5 high |
| `Quiet` | quiet fan | 0 off, **2 on** (what the Gree+ app sends; the classic protocol's 1 is not used) |
| `Tur` | turbo fan | 0 off, 1 on. Only offered in Cool and Heat (the app greys it out in Auto) |
| `SwUpDn` | vertical louvre | 0 off, 1 full swing, 2–6 fixed (top → bottom). The protocol's 7–11 (swing within a region) are **not supported** by `mid` 10001: the unit stores 1 instead |
| `SwingLfRig` | horizontal louvre | 0 off, 1 full swing, 2–6 fixed (left → right) |
| `TemSen` | indoor (return air) temperature | °C **+40** (60 = 20 °C) |
| `OutEnvTem` | outdoor ambient temperature | °C +40 |
| `InEvaTem` | indoor coil temperature | °C +40 (well below room temperature while cooling) |
| `CompressorTem` | compressor temperature | °C +40 |
| `CompressorFqy` | compressor frequency | Hz (0 = compressor stopped) |
| `Lig` | display light | 0/1 |
| `Health` | ioniser ("health") | 0/1 |
| `Blo` | X-fan (coil dry after cooling) | 0/1 |
| `SwhSlp` / `SlpMod` | sleep mode | 0/1 |
| `AllErr` | fault present | 0/1 |

The `+40` offset was confirmed by cross-checking: at a room temperature of 20 °C, `TemSen` was 60;
the coil (`InEvaTem`) read 10 °C (50) while the compressor ran, and about 21 °C (61) while idle.

Other notable fields in the full snapshot: `host` (dispatch server the module was provisioned
with, e.g. `au.dis.gree.com`), `name` (module SSID, e.g. `GR-AC_10001_10_1234_S5`), `hid`
(firmware), `ver`, `mid`, `ModelType`, `wifiStatus`, `busVol`.

Full list of the 145 properties reported by `mid` 10001:

`Wind`, `CpsTem`, `Coolmod`, `JFErrorCode`, `EnvArea7St`, `EnvTem`, `vender`, `EnvArea3St`,
`ElcAllKwhClr`, `DFPoint`, `UnmanedShutDown`, `Slp1L1`–`Slp1L8`, `FbidBloPer`, `VocCtl`,
`VocRole`, `SetTem`, `HeatCoolType`, `Dmod`, `VocIdiom`, `TmrOnMinLf`, `TmrOffMinLf`, `Air`,
`AllErr`, `DsplySt`, `SmartWind`, `Dazzling`, `Pow`, `LigSen`, `TmrOff`, `Blo`, `ReplaceHEPA`,
`Wet`, `Add0.1`, `StHt`, `EnvArea6St`, `HeatCool`, `Add0.5`, `ImgUpdateCol`, `EnvArea2St`,
`AutoClean`, `HasTmr`, `AntiDirectBlow`, `LedLight`, `InEvaTem`, `Dpump`, `SvSt`, `TmrOn`,
`Lig`, `AutoPowReduce`, `SwhSlp`, `EnvFun`, `ModelType`, `TemUn`, `SwUpDn`, `AutoCleanSta`,
`WdSpd`, `Dwet`, `AssHt`, `ChildLock`, `DnPUDSwing`, `MicroSen`, `ImgVerSta`, `EnvArea5St`,
`DnPRLRSwing`, `EnvArea1St`, `CoolFeel`, `BlkTemCom`, `UDFanPort`, `DwatSen`, `Slp1H1`–`Slp1H8`,
`SlpMod`, `NobodySave`, `TmrLpTms`, `DnPLLRSwing`, `DwatFul`, `Quiet`, `header`,
`PowReduceType`, `Emod`, `estateInsta21`–`estateInsta24`, `UnmanedOffTime`, `EnvArea8St`,
`UvcControl`, `EnvArea4St`, `busVol`, `TemSen`, `AutoCleanStaEx`, `OutEnvTem`, `Mod`,
`TemsSenOut`, `Health`, `Dfltr`, `CompressorTem`, `wifiStatus`, `VocVerSta`, `VocUpdateCol`,
`TemRec`, `SmartSlpMod`, `FaultDisplay`, `Tur`, `PowReduceGear`, `SwingLfRig`, `LedLig`,
`PM2P5`, `CompressorFqy`, `EnvArea9St`, `NewTimer`, `wifiReset`, `AppTimer`, `ver`, `ElcEn`,
`AIEnSaveDis`, `host`, `name`, `bc`, `mac`, `mid`, `hid`, `UniqueCode`, `ShutdownFault`,
`SubhealthFault`, `ModelNew`, `WisdomRisk`, `UnmanedSetting`, `OxygenSwitch`, `OxygenDisplay`,
`MMWPosRpt`, `NightLig`.

## Device-side connection

What the unit itself does on the network, from a packet capture on the LAN gateway while a unit
was reset and paired again:

1. DHCP. The request carries only options 53, 57 (max size 1500) and 55 (`1, 3, 28, 6`), with
   no hostname.
2. DNS for `au.dis.gree.com`, sent **directly to `8.8.8.8`**. The resolver handed out by DHCP is
   ignored, so a DNS override on the local router has no effect.
3. TLS to the dispatch server on **port 1813** for about 2 seconds. That server presents a
   certificate from Gree's private CA (`CN=_.as.gree.com`, issuer `CN=iot ca, O=Gree`).
   (It also answers plain TCP on 1812; `{"t":"dis","mac":"<mac>"}` there returns a
   `{"t":"svr",...}` pack, encrypted with the generic key, naming a legacy server on port 16384.)
4. DNS for `mqtt-<region>.gree.com` (again via `8.8.8.8`), then a long-lived **MQTT over TLS**
   connection to that host on **port 1982**. The certificate is again from Gree's private CA
   (`CN=iot devices`), and the broker requires credentials (an anonymous CONNECT gets CONNACK
   code 4). Keep-alive pings every 30 s. App requests reach the unit through this session.
5. Short **plain TCP** connections to the REST host's IP (`augrih.gree.com`) on **port 6100**.
   On each state change the unit sends a 295–327 byte message and receives a 4-byte reply,
   apparently telemetry.

The unit never accepts inbound connections and ignores the app's LAN discovery broadcast.

**WiFi module hardware.** Identified by opening the display box of a GWH24ATEXF-K6DNA1E/I
(Gree **Alto** series, manufactured 2025-04):

| Part | Identification |
| --- | --- |
| Indoor main board | `300002063880` (from Gree Australia's Alto spare parts list) |
| Receiver/display board | `300001061521`, board `D5683AS`, PCB `GRJ568-B V1` (sealed plastic box behind the front panel: 7-segment display, IR receiver `REC1`, `KEY` button, power and WiFi LEDs) |
| WiFi module | **`GRJWB05-J10`**, part `300145000093`, label firmware `WB05DF13V3.10` (matches the `hid` the unit reports). Castellated module (10 pads on each long edge) with a printed antenna, **soldered** onto the display board. FCC ID `2ADAP-GRJWB05J10`, WiFi + BLE. |
| WiFi SoC | **Realtek RTL8720DF** (AmebaD), FreeRTOS, per Gree's TÜV SÜD EN 303 645 certificate, which lists GRJWB04-J and GRJWB05-J5 as 8720CF and GRJWB04-J4, GRJWB05-J2 and GRJWB05-J10 as 8720DF |

**GRJWB05-J10 pads** (from the module's FCC user manual, FCC ID `2ADAP-GRJWB05J10`; 3.3 V supply,
30 × 17.7 mm, 18 castellated pads numbered anticlockwise). Seen from the shield side with the
antenna on the left:

```
Top row, left → right:     16    15    14    13    12    11    10     9       8
                          PB22  MISO   GND  3.3V  MOSI   SCL   SDA  LOG_RX  LOG_TX
Bottom row, left → right:  17    18     1     2     3     4       5      6      7
                           —   PA27   GND  3.3V   ADC  UART_TX UART_RX LP_SDA LP_SCL
```

`UART_TX`/`UART_RX` (pads 4, 5) are the link to the indoor unit; `LOG_TX`/`LOG_RX` (pads 8, 9) are
the AmebaD log UART (PA7/PA8), which is also the ROM download port (PA7 held low at reset).

Notes for anyone hoping for local control:

* There is no separate plug-in WiFi module on this model and no `WIFI` connector on the main
  board; the module lives on the display board, which connects to the main board through a
  5-pin and a 6/7-pin connector (about a dozen wires).
* The display board has no processor of its own: two 16-pin display driver ICs, the IR receiver,
  the button, and pull-up/series resistor networks (4.7 kΩ, 330 Ω, 200 Ω) on the signal lines. The
  WiFi module's UART therefore very likely runs straight to the main board over that cable.
* Released ESPHome (via LibreTiny) supports the RTL8710B and RTL8720**C** families but not yet
  AmebaD (RTL8720**D**). Support is in progress upstream: libretiny-eu/libretiny#403 (AmebaD family),
  libretiny-eu/ltchiptool#98 (flashing) and esphome/esphome#18718 (RTL8720D in ESPHome).
* The main board's `COM-MANUAL` port is for Gree's optional wired controller (4 wires: GND, 12 V,
  RS-485 A/B; see [gree-wired-proto](https://github.com/maxim-smirnov/gree-wired-proto)), not a
  WiFi port.
* The unit's own wiring label (`600007067482`) shows main board `AP2` with connectors `T-SENSOR`,
  `DISP`, `DC-MOTOR`, `COM-MANUAL`, `SWING-UD`, `SWING-LR`, `HEALTH (UVC)`, `DOOR-C (DRY-C)`, `JUMP`,
  and display board `AP1` ("receiver and display board") on `DISP`.

## Open questions

* What, if anything, is published on `event/<mac>/#`.
* Whether `Add0.5` enables half-degree set points in °C on these units.
* The content of the port 6100 telemetry messages.
* Which display-cable pins carry the WiFi module's UART (needs a logic analyser), and whether it
  is the same 4800 8E1 serial protocol older plug-in Gree modules use.
