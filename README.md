# Pool Assistant for Home Assistant

A Home Assistant integration for Lovibond's Pool Assistant app and Scuba3s smart pool photometer - built by reverse-engineering the app's own Firebase backend, since there's no official public API.

## What it does

- Reads live water-chemistry data (Free/Total/Combined Chlorine, pH, Total Alkalinity, Cyanuric Acid, Calcium Hardness, Copper, Salt, Phosphates, Active Oxygen, Bromine) straight from your Pool Assistant account
- Logs manual readings, chemical doses, and notes back to your account - showing up in the app exactly as if you'd entered them there
- Edits or deletes existing readings (including ones scanned via NFC from the Scuba3s) directly from Home Assistant - something the app itself can't always do
- Calculates Langelier Saturation Index (LSI), on demand or automatically if you link a temperature sensor
- Calculates pool volume for rectangular, round, oval, and kidney-shaped pools, using formulas reverse-engineered from the app's own volume calculator
- Creates new pools, and uploads/changes pool photos (from a file path, base64 data, or the Media Browser)
- Automatically discovers pools created directly in the app, and offers a Repair if a pool's been deleted from your account
- Lets you override the "ideal" target range for any parameter, per pool

## Requirements

- A Lovibond Pool Assistant account with at least one pool
- Home Assistant 2024.12 or later

## Installation

### Via HACS (recommended)
1. HACS → ⋮ → Custom repositories → add `https://github.com/YOUR_USERNAME/poolassistant`, category **Integration**
2. Search for "Pool Assistant", install, restart Home Assistant

### Manual
Copy `custom_components/poolassistant` into your Home Assistant `custom_components` folder, then restart.

## Setup

Settings → Devices & Services → Add Integration → Pool Assistant, log in with your Pool Assistant account email/password, and pick which pool(s) to add.

## Services

| Service | What it does |
|---|---|
| `poolassistant.log_reading` | Log a manual water-chemistry reading |
| `poolassistant.add_chemical` | Log a chemical dose, attached to an existing reading |
| `poolassistant.edit_reading` | Change an existing reading's value |
| `poolassistant.delete_reading` | Delete an existing test session |
| `poolassistant.set_volume` | Set a pool's volume |
| `poolassistant.create_pool` | Create a brand new pool |
| `poolassistant.set_pool_image` / `clear_pool_image` | Change or remove a pool's photo |
| `poolassistant.calculate_lsi` | Calculate the Langelier Saturation Index |
| `poolassistant.calculate_volume` | Estimate pool volume from its dimensions |

Full field details are shown in Developer Tools → Actions once installed.

## Disclaimer

This integration talks to Pool Assistant's own Firebase backend, reverse-engineered from the app's own network traffic - it isn't officially supported or endorsed by Lovibond. Things may break if they change their backend.

## License

MIT - see [LICENSE](LICENSE).
