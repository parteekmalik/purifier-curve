"""Custom PM2.5 -> fan speed curve for the Xiaomi Smart Air Purifier 4 Compact (zhimi.airp.cpa4).

Usage:
    uv run purifier_curve.py status   # print current readings once
    uv run purifier_curve.py run      # run the curve loop
"""

import sys
import time
import tomllib
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning, module="miio")

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
CURVE_FILE = HERE / "curve.toml"

MIN_LEVEL, MAX_LEVEL = 0, 14
MODE_FAVORITE = 2
MODE_NAMES = {0: "auto", 1: "sleep", 2: "favorite"}

# MIoT spec: home.miot-spec.com/spec/zhimi.airp.cpa4
PROPS = {
    "power": (2, 1),
    "mode": (2, 4),
    "pm25": (3, 4),
    "level": (9, 11),
}


def level_for(pm25, points):
    """Fan level for a PM2.5 value, interpolating linearly between (pm25, level) points."""
    pts = sorted(points)
    if pm25 <= pts[0][0]:
        level = pts[0][1]
    elif pm25 >= pts[-1][0]:
        level = pts[-1][1]
    else:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= pm25 <= x1:
                level = y0 + (y1 - y0) * (pm25 - x0) / (x1 - x0)
                break
    return max(MIN_LEVEL, min(MAX_LEVEL, round(level)))


class Controller:
    """Steps up immediately; steps down only once the lower target has held for `step_down_after` seconds."""

    def __init__(self, step_down_after):
        self.step_down_after = step_down_after
        self._lower_since = None

    def reset(self):
        self._lower_since = None

    def decide(self, target, device_level, now):
        if target >= device_level:
            self._lower_since = None
            return target if target > device_level else None
        if self._lower_since is None:
            self._lower_since = now
        if now - self._lower_since >= self.step_down_after:
            self._lower_since = None
            return target
        return None


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def load_env():
    if not ENV_FILE.exists():
        sys.exit(f"Missing {ENV_FILE}. Create it with PURIFIER_IP=... and PURIFIER_TOKEN=...")
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in ("PURIFIER_IP", "PURIFIER_TOKEN") if not env.get(k)]
    if missing:
        sys.exit(f"{ENV_FILE} is missing: {', '.join(missing)}")
    ip, token = env["PURIFIER_IP"], env["PURIFIER_TOKEN"]
    if "x" in ip.split(".") or not (len(token) == 32 and all(c in "0123456789abcdefABCDEF" for c in token)):
        sys.exit(f"{ENV_FILE} still has placeholder values. Fill in PURIFIER_IP and the 32-character PURIFIER_TOKEN.")
    return ip, token


def load_curve():
    with CURVE_FILE.open("rb") as f:
        cfg = tomllib.load(f)
    points = [tuple(p) for p in cfg["points"]]
    if len(points) < 2:
        raise ValueError("curve.toml needs at least two points")
    return points, cfg.get("poll_seconds", 10), cfg.get("step_down_after_seconds", 60)


class Purifier:
    def __init__(self, ip, token):
        from miio import Device

        self.dev = Device(ip, token, timeout=5)

    def read(self):
        req = [{"did": name, "siid": s, "piid": p} for name, (s, p) in PROPS.items()]
        result = self.dev.send("get_properties", req)
        values = {}
        for item in result:
            if item.get("code") != 0:
                raise RuntimeError(f"reading {item.get('did')} failed with code {item.get('code')}")
            values[item["did"]] = item["value"]
        return values

    def set_level(self, level):
        s, p = PROPS["level"]
        result = self.dev.send("set_properties", [{"did": "level", "siid": s, "piid": p, "value": level}])
        if result[0].get("code") != 0:
            raise RuntimeError(f"setting level failed with code {result[0].get('code')}")


def describe(v):
    return (
        f"power={'on' if v['power'] else 'off'} mode={MODE_NAMES.get(v['mode'], v['mode'])} "
        f"pm2.5={v['pm25']} level={v['level']}"
    )


def cmd_status():
    print(describe(Purifier(*load_env()).read()))


def cmd_run():
    purifier = Purifier(*load_env())
    points, poll, step_down = load_curve()
    controller = Controller(step_down)
    log(f"started: curve={points} poll={poll}s step_down_after={step_down}s")
    last_state = None
    failures = 0

    while True:
        try:
            points, poll, controller.step_down_after = load_curve()
        except Exception as e:
            log(f"curve.toml invalid, keeping previous curve: {e}")

        try:
            v = purifier.read()
            failures = 0
        except Exception as e:
            failures += 1
            log(f"cannot reach purifier ({failures}): {e}")
            time.sleep(min(poll * failures, 120))
            continue

        if not v["power"] or v["mode"] != MODE_FAVORITE:
            state = "off" if not v["power"] else MODE_NAMES.get(v["mode"], v["mode"])
            if state != last_state:
                log(f"paused (purifier is {state}); switch to favorite mode to use the curve")
            last_state = state
            controller.reset()
            time.sleep(poll)
            continue

        if last_state != "active":
            log(f"active: {describe(v)}")
            last_state = "active"

        target = level_for(v["pm25"], points)
        new_level = controller.decide(target, v["level"], time.monotonic())
        if new_level is not None:
            try:
                purifier.set_level(new_level)
                log(f"pm2.5={v['pm25']} level {v['level']} -> {new_level}")
            except Exception as e:
                log(f"failed to set level {new_level}: {e}")

        time.sleep(poll)


if __name__ == "__main__":
    commands = {"status": cmd_status, "run": cmd_run}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    try:
        commands[sys.argv[1]]()
    except KeyboardInterrupt:
        pass
