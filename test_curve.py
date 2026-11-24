from purifier_curve import Controller, level_for, pick_purifier, set_env_value, tick

POINTS = [(5, 2), (12, 5), (25, 9), (35, 12), (50, 14)]


def test_below_first_point_uses_first_level():
    assert level_for(0, POINTS) == 2
    assert level_for(5, POINTS) == 2


def test_above_last_point_uses_last_level():
    assert level_for(50, POINTS) == 14
    assert level_for(400, POINTS) == 14


def test_exact_points():
    assert level_for(25, POINTS) == 9
    assert level_for(35, POINTS) == 12


def test_interpolates_between_points():
    # halfway between (25, 9) and (35, 12) -> 10.5 rounds to 10 or 11; check bounds
    assert level_for(30, POINTS) in (10, 11)
    # 3/7 of the way from (5, 2) to (12, 5) -> 3.29 -> 3
    assert level_for(8, POINTS) == 3


def test_unsorted_points_are_handled():
    assert level_for(30, list(reversed(POINTS))) == level_for(30, POINTS)


def test_level_clamped_to_device_range():
    assert level_for(100, [(0, -3), (50, 20)]) == 14
    assert level_for(0, [(0, -3), (50, 20)]) == 0


def test_steps_up_immediately():
    c = Controller(step_down_after=60)
    assert c.decide(target=10, device_level=5, now=0) == 10


def test_no_change_when_already_at_target():
    c = Controller(step_down_after=60)
    assert c.decide(target=5, device_level=5, now=0) is None


def test_steps_down_only_after_delay():
    c = Controller(step_down_after=60)
    assert c.decide(target=3, device_level=8, now=0) is None
    assert c.decide(target=3, device_level=8, now=30) is None
    assert c.decide(target=3, device_level=8, now=60) == 3


def test_step_down_timer_resets_when_air_gets_worse_again():
    c = Controller(step_down_after=60)
    assert c.decide(target=3, device_level=8, now=0) is None
    assert c.decide(target=8, device_level=8, now=40) is None
    assert c.decide(target=3, device_level=8, now=50) is None
    assert c.decide(target=3, device_level=8, now=100) is None
    assert c.decide(target=3, device_level=8, now=110) == 3


def test_reset_clears_pending_step_down():
    c = Controller(step_down_after=60)
    c.decide(target=3, device_level=8, now=0)
    c.reset()
    assert c.decide(target=3, device_level=8, now=60) is None


def test_pick_purifier_returns_device_that_accepts_token_and_matches_model():
    models = {"10.0.0.2": "yeelink.light.color1", "10.0.0.3": "xiaomi.airp.cpa4m"}
    assert pick_purifier(["10.0.0.2", "10.0.0.3"], models.__getitem__) == ("10.0.0.3", "xiaomi.airp.cpa4m")


def test_pick_purifier_accepts_zhimi_variant():
    assert pick_purifier(["10.0.0.5"], lambda ip: "zhimi.airp.cpa4") == ("10.0.0.5", "zhimi.airp.cpa4")


def test_pick_purifier_skips_devices_that_reject_the_token():
    def probe(ip):
        if ip == "10.0.0.2":
            raise TimeoutError("wrong token")
        return "xiaomi.airp.cpa4m"

    assert pick_purifier(["10.0.0.2", "10.0.0.3"], probe) == ("10.0.0.3", "xiaomi.airp.cpa4m")


def test_pick_purifier_returns_none_when_nothing_matches():
    assert pick_purifier([], lambda ip: "x") is None
    assert pick_purifier(["10.0.0.2"], lambda ip: "yeelink.light.color1") is None


def test_set_env_value_replaces_existing_line_and_keeps_the_rest():
    text = "# comment\nPURIFIER_IP=192.168.1.5\nPURIFIER_TOKEN=abc\n"
    assert set_env_value(text, "PURIFIER_IP", "192.168.1.9") == "# comment\nPURIFIER_IP=192.168.1.9\nPURIFIER_TOKEN=abc\n"


def test_set_env_value_appends_when_missing():
    assert set_env_value("PURIFIER_TOKEN=abc\n", "PURIFIER_IP", "10.0.0.3") == "PURIFIER_TOKEN=abc\nPURIFIER_IP=10.0.0.3\n"
    assert set_env_value("PURIFIER_TOKEN=abc", "PURIFIER_IP", "10.0.0.3") == "PURIFIER_TOKEN=abc\nPURIFIER_IP=10.0.0.3\n"


class FakePurifier:
    def __init__(self, power=True, mode=2, pm25=30, level=5):
        self.v = {"power": power, "mode": mode, "pm25": pm25, "level": level}
        self.calls = []

    def read(self):
        return dict(self.v)

    def set_mode(self, mode):
        self.calls.append(("mode", mode))
        self.v["mode"] = mode

    def set_level(self, level):
        self.calls.append(("level", level))
        self.v["level"] = level


def test_tick_forces_favorite_from_auto_and_applies_curve_immediately():
    dev = FakePurifier(mode=0, pm25=35, level=3)
    tick(dev, Controller(step_down_after=60), POINTS, now=0)
    assert dev.calls == [("mode", 2), ("level", 12)]


def test_tick_forces_favorite_from_sleep_even_when_curve_wants_lower_level():
    dev = FakePurifier(mode=1, pm25=5, level=10)
    tick(dev, Controller(step_down_after=60), POINTS, now=0)
    assert dev.calls == [("mode", 2), ("level", 2)]


def test_tick_never_turns_purifier_on():
    dev = FakePurifier(power=False, mode=0)
    tick(dev, Controller(step_down_after=60), POINTS, now=0)
    assert dev.calls == []


def test_tick_in_favorite_only_changes_level():
    dev = FakePurifier(mode=2, pm25=35, level=3)
    tick(dev, Controller(step_down_after=60), POINTS, now=0)
    assert dev.calls == [("level", 12)]


def test_tick_in_favorite_waits_before_stepping_down():
    dev = FakePurifier(mode=2, pm25=5, level=10)
    c = Controller(step_down_after=60)
    tick(dev, c, POINTS, now=0)
    assert dev.calls == []
    tick(dev, c, POINTS, now=60)
    assert dev.calls == [("level", 2)]


def test_tick_returns_messages_describing_changes():
    dev = FakePurifier(mode=0, pm25=35, level=3)
    msgs = tick(dev, Controller(step_down_after=60), POINTS, now=0)
    assert any("auto" in m and "favorite" in m for m in msgs)
    assert any("3 -> 12" in m for m in msgs)
