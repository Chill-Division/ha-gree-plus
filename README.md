# Gree+ for Home Assistant

A Home Assistant integration for current-generation **Gree** air conditioners that are set up
in the **Gree+** app and can **only** be controlled through Gree's cloud.

Older Gree units can be controlled on the local network (UDP port 7000), which is what Home
Assistant's built-in [Gree integration](https://www.home-assistant.io/integrations/gree/) and
most custom components rely on. Newer WiFi modules (for example firmware V3.10, model ID `10001`)
no longer answer on the LAN at all, so those integrations can't find them. This integration
talks to the same cloud MQTT service the Gree+ app uses instead.

> [!NOTE]
> This is an unofficial project, not affiliated with or endorsed by Gree Electric Appliances.

## Features

Each unit in your Gree+ account becomes a Home Assistant device with:

**Climate entity**

| Control | Options |
| --- | --- |
| Power | on / off |
| Mode | Auto, Cool, Dry, Fan only, Heat |
| Target temperature | 16–30 °C in 1 °C steps |
| Fan | Auto, Low, Medium-low, Medium, Medium-high, High, Quiet, Turbo (Turbo in Cool/Heat only, as in the app) |
| Vertical swing (up–down) | Off, full swing, 5 fixed positions |
| Horizontal swing (left–right) | Off, full swing, 5 fixed positions |

The climate entity also reports the current indoor temperature and an *HVAC action*
(cooling / heating / drying / fan / idle) derived from the compressor and coil readings.

**Sensors**

* Indoor temperature
* Outdoor temperature
* Compressor frequency (diagnostic)
* Indoor coil temperature (diagnostic, disabled by default)

State changes are pushed by the unit within a few seconds, including changes made from the
remote control or the Gree+ app. The integration also polls every 60 seconds as a safety net.

## Requirements

* Home Assistant 2025.2 or newer.
* Your units are already set up in the **Gree+** app and work there.
* The email and password for that Gree+ account. If you signed up with Google, set a password in
  the Gree+ app first.

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/Chill-Division/ha-gree-plus` with type **Integration**.
3. Search for **Gree+**, install it, and restart Home Assistant.

### Manual

Copy `custom_components/gree_plus` into your Home Assistant `config/custom_components/`
folder and restart Home Assistant.

## Configuration

1. **Settings → Devices & services → Add integration → Gree+**.
2. Enter your Gree+ email and password.
3. Pick your server region, or leave **Auto-detect** selected. Accounts registered with the
   *New Zealand* region in the app live on the **Australia** server.

Every air conditioner in the account (all homes and rooms) is added. Units you add to the
Gree+ app later appear after reloading the integration.

If you change your Gree+ password, Home Assistant will ask you to re-authenticate.

Re-pairing a unit in the Gree+ app gives it a new encryption key; the integration notices and
fetches the new key by itself. A re-paired unit may come back switched off, so check its settings.

## Notes and limitations

* **Cloud dependent.** Control needs both Home Assistant and the unit to reach Gree's servers.
  If the internet or Gree's cloud is down, the unit keeps running its last settings and its
  own thermostat, and can still be used with the IR remote.
* Using the integration does not log the Gree+ app out; both can be used side by side.
* Only Celsius set points are supported.
* Tested with Gree units on model ID `10001`, WiFi firmware V3.10, Gree+ region New Zealand.
  Other Gree+ units that use the same cloud protocol should work. Please open an issue with your
  diagnostics if yours doesn't.

## Protocol documentation

How these units communicate (REST API, MQTT topics, envelopes, encryption, property tables) is
documented in [docs/PROTOCOL.md](docs/PROTOCOL.md) for anyone building on the same devices.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.gree_plus: debug
```

The integration's **Download diagnostics** option (on the integration's ⋮ menu) produces a
report with your password, token and device keys redacted.

## Credits

* [luc10/gree-api-client](https://github.com/luc10/gree-api-client): Gree cloud REST API.
* [tomikaa87/gree-remote](https://github.com/tomikaa87/gree-remote): the Gree property
  vocabulary (`Pow`, `Mod`, `SwUpDn`, ...).
* [davo22/greeclimate](https://github.com/davo22/greeclimate): earlier cloud MQTT work.

## License

[MIT](LICENSE)
