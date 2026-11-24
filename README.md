# purifier-curve

A custom, editable PM2.5 → fan speed curve for the **Xiaomi Smart Air Purifier 4 Compact** (`xiaomi.airp.cpa4m` / `zhimi.airp.cpa4`).

The stock Auto mode treats PM2.5 up to 35 µg/m³ as "good" and idles the fan, so the air tends to sit around 35. This script runs on a computer on the same network, reads the purifier's PM2.5 over local Wi-Fi, and sets the fan level (0–14) from your own curve. No cloud, no firmware changes, no opening the device.

## How it works

- Polls the purifier every few seconds over the local miIO protocol.
- Picks a fan level from the points in `curve.toml`, interpolating between them.
- Speeds up immediately when air gets worse; slows down only after the lower level has held for a while, so the fan doesn't hunt.
- Keeps the purifier in **Favorite** mode, where the fan level can be set directly. If it's switched to Auto or Sleep (button or app), the script switches it back and applies the curve right away. To use another mode, stop the script.
- Never turns the purifier on: turning it off pauses the curve until it's turned back on.
- Finds the purifier on the network by itself, and finds it again if its IP changes.

## Setup

1. Get the device's local token with [Xiaomi Cloud Tokens Extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).
2. Copy the env template and fill in the token:
   ```bash
   cp .env.example .env
   chmod 600 .env
   ```
3. Find the purifier and check the connection (requires [uv](https://docs.astral.sh/uv/)):
   ```bash
   uv run purifier_curve.py discover
   uv run purifier_curve.py status
   ```

### Finding the device

`discover` sends a Xiaomi "hello" broadcast on the local network, then confirms each reply by checking it accepts your token and reports a 4 Compact model, so other Xiaomi devices are ignored. The IP is saved to `.env`. You never need to run it by hand: `status` and `run` discover automatically when no IP is set, and `run` searches again after 3 failed reads in a row. Reserving the IP in your router is still a good idea, but no longer required.
4. Run the curve (it switches the purifier to Favorite mode itself):
   ```bash
   uv run purifier_curve.py run
   ```

## Tuning the curve

Edit `curve.toml` while it runs; changes apply on the next poll.

```toml
points = [[5, 2], [12, 5], [25, 9], [35, 12], [50, 14]]  # [PM2.5, fan level 0-14]
poll_seconds = 10
step_down_after_seconds = 60
```

## Tests

```bash
uv run pytest
```
