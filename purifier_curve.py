"""Custom PM2.5 -> fan speed curve for the Xiaomi Smart Air Purifier 4 Compact
(xiaomi.airp.cpa4m / zhimi.airp.cpa4).

Usage:
    uv run purifier_curve.py discover # find the purifier on the network and save its IP to .env
    uv run purifier_curve.py status   # print current readings once
    uv run purifier_curve.py run      # run the curve loop
"""

import socket
import sys
import time
import tomllib
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
CURVE_FILE = HERE / "curve.toml"

MIN_LEVEL, MAX_LEVEL = 0, 14
MODE_FAVORITE = 2
MODE_NAMES = {0: "auto", 1: "sleep", 2: "favorite"}

SUPPORTED_MODELS = ("xiaomi.airp.cpa4", "zhimi.airp.cpa4")
MIIO_PORT = 54321
FAILURES_BEFORE_REDISCOVERY = 3

# MIoT spec (same IDs for both models): home.miot-spec.com/spec/xiaomi.airp.cpa4m
PROPS = {
    "power": (2, 1),
    "mode": (2, 4),
    "pm25": (3, 4),
    "level": (9, 11),
    "sound": (6, 1),
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
    """Return (ip or None, token). A missing or placeholder IP means "discover it"."""
    if not ENV_FILE.exists():
        sys.exit(f"Missing {ENV_FILE}. Copy .env.example to .env and fill in PURIFIER_TOKEN.")
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    token = env.get("PURIFIER_TOKEN", "")
    if not (len(token) == 32 and all(c in "0123456789abcdefABCDEF" for c in token)):
        sys.exit(f"{ENV_FILE} needs the 32-character PURIFIER_TOKEN.")
    ip = env.get("PURIFIER_IP") or None
    if ip and "x" in ip.split("."):
        ip = None
    return ip, token


def set_env_value(text, key, value):
    """Set KEY=value in .env text, replacing an existing line or appending one."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def save_ip(ip):
    ENV_FILE.write_text(set_env_value(ENV_FILE.read_text(), "PURIFIER_IP", ip))


def broadcast_addresses():
    import netifaces

    addrs = set()
    for iface in netifaces.interfaces():
        for a in netifaces.ifaddresses(iface).get(netifaces.AF_INET, []):
            if a.get("broadcast") and not a["addr"].startswith(("127.", "169.254.")):
                addrs.add(a["broadcast"])
    return addrs or {"255.255.255.255"}


def find_miio_devices(timeout=3):
    """IPs of all Xiaomi devices on the local network that answer a miIO hello."""
    hello = bytes.fromhex("21310020" + "ff" * 28)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.5)
    found = []
    try:
        for _ in range(3):
            for addr in broadcast_addresses():
                s.sendto(hello, (addr, MIIO_PORT))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, (ip, _) = s.recvfrom(1024)
            except socket.timeout:
                continue
            if data[:2] == b"\x21\x31" and ip not in found:
                found.append(ip)
    finally:
        s.close()
    return found


def pick_purifier(ips, probe):
    """First (ip, model) whose device accepts our token (probe succeeds) and is a supported model."""
    for ip in ips:
        try:
            model = probe(ip)
        except Exception:
            continue
        if model.startswith(SUPPORTED_MODELS):
            return ip, model
    return None


def discover(token):
    from miio import Device

    def probe(ip):
        return Device(ip, token, timeout=2).send("miIO.info", retry_count=0)["model"]

    return pick_purifier(find_miio_devices(), probe)


def load_curve():
    with CURVE_FILE.open("rb") as f:
        cfg = tomllib.load(f)
    points = [tuple(p) for p in cfg["points"]]
    if len(points) < 2:
        raise ValueError("curve.toml needs at least two points")
    return points, cfg.get("poll_seconds", 10), cfg.get("step_down_after_seconds", 60), bool(cfg.get("sound", False))


class Purifier:
    def __init__(self, ip, token):
        from miio import Device

        self.ip = ip
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

    def _set(self, name, value):
        s, p = PROPS[name]
        result = self.dev.send("set_properties", [{"did": name, "siid": s, "piid": p, "value": value}])
        if result[0].get("code") != 0:
            raise RuntimeError(f"setting {name} failed with code {result[0].get('code')}")

    def set_level(self, level):
        self._set("level", level)

    def set_mode(self, mode):
        self._set("mode", mode)

    def set_sound(self, on):
        self._set("sound", on)


def tick(purifier, controller, points, now, sound=False):
    """One control cycle: enforce the sound setting and Favorite mode, then apply the curve.
    Returns log messages.

    Never turns the purifier on; turning it off is how you stop the curve.
    """
    v = purifier.read()
    if not v["power"]:
        controller.reset()
        return ["purifier is off; waiting for it to be turned on"]

    msgs = []
    if v["sound"] != sound:
        purifier.set_sound(sound)
        msgs.append(f"sound {'on' if v['sound'] else 'off'} -> {'on' if sound else 'off'}")

    target = level_for(v["pm25"], points)
    if v["mode"] != MODE_FAVORITE:
        purifier.set_mode(MODE_FAVORITE)
        msgs.append(f"switched mode {MODE_NAMES.get(v['mode'], v['mode'])} -> favorite")
        controller.reset()
        new_level = target if target != v["level"] else None
    else:
        new_level = controller.decide(target, v["level"], now)

    if new_level is not None:
        purifier.set_level(new_level)
        msgs.append(f"pm2.5={v['pm25']} level {v['level']} -> {new_level}")
    return msgs


def describe(v):
    return (
        f"power={'on' if v['power'] else 'off'} mode={MODE_NAMES.get(v['mode'], v['mode'])} "
        f"pm2.5={v['pm25']} level={v['level']} sound={'on' if v['sound'] else 'off'}"
    )


def rediscover(token, old_ip):
    """Look for the purifier on the network; save and return its IP if found, else None."""
    found = discover(token)
    if not found:
        return None
    ip, model = found
    if ip != old_ip:
        save_ip(ip)
        log(f"found {model} at {ip} (was {old_ip or 'not set'}); saved to .env")
    return ip


def connect():
    """Purifier from .env, discovering the IP if it isn't set."""
    ip, token = load_env()
    if not ip:
        ip = rediscover(token, None)
        if not ip:
            sys.exit("Purifier not found on the network. Is it powered on and on the same Wi-Fi as this Mac?")
    return Purifier(ip, token), token


def cmd_discover():
    _, token = load_env()
    found = discover(token)
    if not found:
        sys.exit("Purifier not found on the network. Is it powered on and on the same Wi-Fi as this Mac?")
    ip, model = found
    save_ip(ip)
    print(f"Found {model} at {ip}; saved to .env")


def cmd_status():
    purifier, token = connect()
    try:
        v = purifier.read()
    except Exception as e:
        print(f"Cannot reach {purifier.ip} ({e}); searching the network...")
        ip = rediscover(token, purifier.ip)
        if not ip:
            sys.exit("Purifier not found on the network. Is it powered on and on the same Wi-Fi as this Mac?")
        purifier = Purifier(ip, token)
        v = purifier.read()
    print(f"{purifier.ip}: {describe(v)}")


def cmd_run():
    purifier, token = connect()
    points, poll, step_down, sound = load_curve()
    controller = Controller(step_down)
    log(
        f"started at {purifier.ip}: curve={points} poll={poll}s "
        f"step_down_after={step_down}s sound={'on' if sound else 'off'}"
    )
    last_msgs = None
    failures = 0

    while True:
        try:
            points, poll, controller.step_down_after, sound = load_curve()
        except Exception as e:
            log(f"curve.toml invalid, keeping previous curve: {e}")

        try:
            msgs = tick(purifier, controller, points, time.monotonic(), sound=sound)
            failures = 0
        except Exception as e:
            failures += 1
            log(f"cannot reach purifier at {purifier.ip} ({failures}): {e}")
            if failures % FAILURES_BEFORE_REDISCOVERY == 0:
                ip = rediscover(token, purifier.ip)
                if ip and ip != purifier.ip:
                    purifier = Purifier(ip, token)
                    failures = 0
                    continue
            time.sleep(min(poll * failures, 120))
            continue

        if msgs != last_msgs:
            for m in msgs:
                log(m)
        last_msgs = msgs
        time.sleep(poll)


if __name__ == "__main__":
    commands = {"discover": cmd_discover, "status": cmd_status, "run": cmd_run}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    try:
        commands[sys.argv[1]]()
    except KeyboardInterrupt:
        pass
