from purifier_curve import Controller, level_for

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
