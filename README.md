# purifier-curve

A custom, editable PM2.5 → fan speed curve for the **Xiaomi Smart Air Purifier 4 Compact** (`zhimi.airp.cpa4`).

The stock Auto mode treats PM2.5 up to 35 µg/m³ as "good" and idles the fan, so the air tends to sit around 35. This script runs on a computer on the same network, reads the purifier's PM2.5 over local Wi-Fi, and sets the fan level (0–14) from your own curve. No cloud, no firmware changes, no opening the device.

## How it works

- Polls the purifier every few seconds over the local miIO protocol.
- Picks a fan level from the points in `curve.toml`, interpolating between them.
- Speeds up immediately when air gets worse; slows down only after the lower level has held for a while, so the fan doesn't hunt.
- Only acts while the purifier is in **Favorite** mode. Switch to Auto or Sleep (button or app) and it steps aside; it never turns the purifier on.

## Setup

1. Get the device's local token and IP with [Xiaomi Cloud Tokens Extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor). Reserve the IP in your router.
2. Copy the env template and fill it in:
   ```bash
   cp .env.example .env
   chmod 600 .env
   ```
3. Check the connection (requires [uv](https://docs.astral.sh/uv/)):
   ```bash
   uv run purifier_curve.py status
   ```
4. Put the purifier in Favorite mode and run the curve:
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
