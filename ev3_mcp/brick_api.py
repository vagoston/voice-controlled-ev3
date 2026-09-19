"""Brick-side API that skills are written against.

Runs on the EV3 under MicroPython (ev3dev), never imported on the host -- the
SSH runner ships it as text. Target Python 3.5 / MicroPython: no f-strings, no
modern typing syntax.

The host prepends _MAX_DURATION, _BUDGET_S and _PROFILE (the hardware profile
from robot.toml) before this source, so those names already exist when this
executes.
"""

import time

_devices = {}
_started = time.time()

_MOTORS = _PROFILE["motors"]
_LEFT_PORT = _MOTORS["left"]["port"] if "left" in _MOTORS else None
_RIGHT_PORT = _MOTORS["right"]["port"] if "right" in _MOTORS else None
_REVERSED = _PROFILE.get("reversed", [])
_LEFT_SIGN = -1 if "left" in _REVERSED else 1
_RIGHT_SIGN = -1 if "right" in _REVERSED else 1

_GEOMETRY = _PROFILE.get("geometry", {})
_WHEEL_MM = _GEOMETRY.get("wheel_diameter_mm")
_TRACK_MM = _GEOMETRY.get("axle_track_mm")


def _clamp(value, low, high):
    return max(low, min(value, high))


def time_left():
    """Seconds remaining in this run's budget before the host kills it."""
    return max(0.0, _BUDGET_S - (time.time() - _started))


def out_of_time():
    """True once the budget is spent. Poll this in long loops and exit cleanly."""
    return time_left() <= 0.0


def _bounded(seconds):
    """Clamp a duration to the sanity cap and to whatever budget remains."""
    return _clamp(float(seconds), 0.0, min(_MAX_DURATION, time_left()))


def _speed(percent):
    """Validity bound only -- the hardware's real range, not a safety policy."""
    return _clamp(float(percent), -100.0, 100.0)


def _port(letter):
    from ev3dev2.motor import OUTPUT_A, OUTPUT_B, OUTPUT_C, OUTPUT_D

    mapping = {"A": OUTPUT_A, "B": OUTPUT_B, "C": OUTPUT_C, "D": OUTPUT_D}
    key = str(letter).upper()
    if key not in mapping:
        raise ValueError("Invalid motor port: {!r}".format(letter))
    return mapping[key]


def _tank():
    if _LEFT_PORT is None or _RIGHT_PORT is None:
        raise ValueError(
            "This robot has no left/right drive motors. "
            "Named motors are: {}. Use motor(name, ...) instead of drive().".format(
                ", ".join(sorted(_MOTORS)) or "none"
            )
        )
    if "tank" not in _devices:
        from ev3dev2.motor import MoveTank

        _devices["tank"] = MoveTank(_port(_LEFT_PORT), _port(_RIGHT_PORT))
    return _devices["tank"]


def _input(number):
    from ev3dev2.sensor import INPUT_1, INPUT_2, INPUT_3, INPUT_4

    return {1: INPUT_1, 2: INPUT_2, 3: INPUT_3, 4: INPUT_4}[number]


def _sensor(kind, factory):
    """Instantiate a profiled sensor once.

    A sensor the profile does not list is reported absent without probing for
    it, so a skill asking for one gets told plainly instead of guessing.
    """
    if kind not in _devices:
        number = _PROFILE.get("sensors", {}).get(kind)
        if number is None:
            _devices[kind] = None
        else:
            try:
                _devices[kind] = factory(_input(number))
            except Exception:
                _devices[kind] = None
    return _devices[kind]


def log(message):
    print(message)


def sleep(seconds):
    time.sleep(_bounded(seconds))


def drive(left_pct, right_pct, seconds=None):
    """Run both drive motors. Blocks for `seconds`, then stops; else returns immediately."""
    # Resolve the motors first, so a profile without drive motors says so
    # instead of failing somewhere less obvious.
    tank = _tank()
    from ev3dev2.motor import SpeedPercent

    left = SpeedPercent(_speed(left_pct) * _LEFT_SIGN)
    right = SpeedPercent(_speed(right_pct) * _RIGHT_SIGN)
    if seconds is None:
        tank.on(left, right)
    else:
        tank.on_for_seconds(left, right, _bounded(seconds))


def _require_geometry():
    if _WHEEL_MM is None or _TRACK_MM is None:
        raise ValueError(
            "This robot's geometry is not measured, so distances and angles "
            "cannot be computed. Use drive()/forward() with durations and say "
            "the result is approximate."
        )


def mm_per_wheel_degree():
    _require_geometry()
    return (3.141592653589793 * _WHEEL_MM) / 360.0


def wheel_degrees_per_robot_degree():
    """Counter-rotating both wheels, this reduces to track / diameter."""
    _require_geometry()
    return _TRACK_MM / float(_WHEEL_MM)


def travelled_cm(before, after):
    """Straight-line distance between two wheel_degrees() readings."""
    left_fwd = (after[0] - before[0]) * _LEFT_SIGN
    right_fwd = (after[1] - before[1]) * _RIGHT_SIGN
    return ((left_fwd + right_fwd) / 2.0) * mm_per_wheel_degree() / 10.0


def _run_until(left_speed, right_speed, reached):
    """Run both drive motors until `reached()` says so, then brake.

    Closed on our own encoder readings rather than ev3dev2's position targets,
    which were measured to aim at a stale absolute position that survives a
    reset, and to refuse to counter-rotate at all.
    """
    from ev3dev2.motor import SpeedPercent

    tank = _tank()
    tank.on(SpeedPercent(_speed(left_speed)), SpeedPercent(_speed(right_speed)))
    while not reached() and not out_of_time():
        time.sleep(0.01)
    tank.off(brake=True)
    time.sleep(0.15)  # let it settle before the closing measurement


def drive_cm(cm, speed_pct=40):
    """Drive straight. Returns the cm actually travelled, from the encoders.

    Compare the return value with what you asked for and report the difference
    rather than assuming it went where you said.
    """
    target = abs(float(cm))
    direction = 1 if cm >= 0 else -1
    magnitude = _speed(abs(speed_pct))
    before = wheel_degrees()

    def reached():
        return abs(travelled_cm(before, wheel_degrees())) >= target

    _run_until(
        magnitude * direction * _LEFT_SIGN,
        magnitude * direction * _RIGHT_SIGN,
        reached,
    )
    return travelled_cm(before, wheel_degrees())


def turn(degrees, speed_pct=30):
    """Turn on the spot. Positive is clockwise seen from above.

    Returns the degrees actually turned according to the encoders, which will
    differ from the request when the wheels slip.
    """
    target = abs(float(degrees))
    direction = 1 if degrees >= 0 else -1
    magnitude = _speed(abs(speed_pct))
    before = wheel_degrees()

    def reached():
        return abs(turned_degrees(before, wheel_degrees())) >= target

    _run_until(
        magnitude * direction * _LEFT_SIGN,
        -magnitude * direction * _RIGHT_SIGN,
        reached,
    )
    return turned_degrees(before, wheel_degrees())


def turned_degrees(before, after):
    """Heading change between two wheel_degrees() readings.

    Uses the difference between the wheels rather than their average. An
    average reads a half-finished turn as a whole one, because it cannot tell
    counter-rotation from one wheel doing all the work.
    """
    ratio = wheel_degrees_per_robot_degree()
    left_fwd = (after[0] - before[0]) * _LEFT_SIGN
    right_fwd = (after[1] - before[1]) * _RIGHT_SIGN
    return (left_fwd - right_fwd) / (2.0 * ratio)


def forward(speed_pct=40, seconds=1.0):
    drive(speed_pct, speed_pct, seconds)


def backward(speed_pct=40, seconds=1.0):
    drive(-speed_pct, -speed_pct, seconds)


def spin_left(speed_pct=30, seconds=0.5):
    drive(-speed_pct, speed_pct, seconds)


def spin_right(speed_pct=30, seconds=0.5):
    drive(speed_pct, -speed_pct, seconds)


def resolve_port(ref):
    """Port letter for a motor name from the profile, or a bare port letter."""
    key = str(ref).lower()
    if key in _MOTORS:
        return _MOTORS[key]["port"]
    return str(ref).upper()


def motor_roles():
    """Names this robot's motors answer to, from the profile."""
    return sorted(_MOTORS.keys())


def gear_ratio(name):
    """Motor degrees per degree of whatever that motor drives."""
    entry = _MOTORS.get(str(name).lower())
    return entry.get("gear_ratio", 1.0) if entry else 1.0


def _push_to_limit(device, direction, speed_pct, budget=4.0):
    """Drive until the mechanism stops moving, then cut power.

    Uses real torque on purpose. Gentle pulses cannot overcome static friction
    and report a limit in open air: a sweep at speed 12 measured the head's
    travel as 28 degrees where it is really 65.
    """
    from ev3dev2.motor import SpeedPercent

    device.on(SpeedPercent(_speed(abs(speed_pct)) * direction))
    started = time.time()
    last_pos = device.position
    last_move = started
    while time.time() - started < budget and not out_of_time():
        position = device.position
        if position != last_pos:
            last_pos = position
            last_move = time.time()
        elif time.time() - last_move > 0.25:
            break
        time.sleep(0.02)
    # Coast rather than brake: never hold torque against a hard stop.
    device.off(brake=False)
    time.sleep(0.25)
    return device.position


def _goto(device, target, speed_pct, budget=6.0):
    """Drive a motor to an absolute encoder position.

    on_to_position takes an absolute target and decelerates into it, landing
    within a degree from either direction. Do not replace this with repeated
    short moves: the motor cannot make a move smaller than roughly six
    degrees, so a correction loop oscillates around the target instead of
    converging, and stops wherever it happens to be when it gives up.

    Absolute targeting is reliable even though on_for_degrees is not -- that
    one derives its target from a stale position reading.
    """
    from ev3dev2.motor import SpeedPercent

    goal = int(round(target))
    device.stop_action = "brake"
    for _ in range(3):
        device.on_to_position(SpeedPercent(_speed(abs(speed_pct))), goal, block=False)
        # The driver takes a moment to report itself running. Polling before
        # then sees is_running False, cuts the move off mid-deceleration and
        # lets it coast past the target.
        time.sleep(0.05)
        deadline = time.time() + budget
        while device.is_running and time.time() < deadline and not out_of_time():
            time.sleep(0.02)
        time.sleep(0.25)
        if abs(device.position - goal) <= 2 or out_of_time():
            break
    return device.position


def centre_motor(name="head", speed_pct=40):
    """Find a limited motor's two stops and park it midway between them.

    The encoder zeroes wherever the motor happened to be at power-on, so the
    middle has to be rediscovered each boot -- there is no position worth
    storing. Returns a dict describing what it found, in output degrees.
    """
    from ev3dev2.motor import SpeedPercent

    device = _motor(name)
    if device is None:
        raise ValueError(
            "No motor called {!r}. This robot has: {}".format(
                name, ", ".join(motor_roles())
            )
        )

    ratio = gear_ratio(name)
    positive = _push_to_limit(device, 1, speed_pct)
    time.sleep(0.2)
    negative = _push_to_limit(device, -1, speed_pct)
    time.sleep(0.2)

    middle = (positive + negative) / 2.0
    _goto(device, middle, speed_pct)

    span = positive - negative
    return {
        "centre": middle,
        "position": device.position,
        "offset_degrees": (device.position - middle) / ratio,
        "span_degrees": span / ratio,
        "limits_motor": (negative, positive),
    }


def _motor(port):
    key = resolve_port(port)
    address = _port(key)
    if key not in _devices:
        try:
            from ev3dev2.motor import Motor

            _devices[key] = Motor(address)
        except Exception:
            _devices[key] = None
    return _devices[key]


def motor(port, speed_pct=30, seconds=1.0):
    """Run one motor by role name ('head') or port letter. False when absent."""
    from ev3dev2.motor import SpeedPercent

    device = _motor(port)
    if device is None:
        return False
    device.on_for_seconds(SpeedPercent(_speed(speed_pct)), _bounded(seconds))
    return True


def motor_position(port):
    """Encoder position in degrees for one motor port, or None when empty."""
    device = _motor(port)
    return None if device is None else device.position


def stop():
    _tank().off(brake=True)


def _safe_stop():
    """Appended by the host after every program: motors must not outlive a run."""
    try:
        stop()
    except Exception:
        pass


def distance_cm():
    """Ultrasonic distance in cm, or None when no ultrasonic sensor is attached."""

    def make(addr):
        from ev3dev2.sensor.lego import UltrasonicSensor

        return UltrasonicSensor(addr)

    sensor = _sensor("ultrasonic", make)
    return None if sensor is None else sensor.distance_centimeters


def proximity():
    """Infrared proximity 0-100 (lower is closer), or None when no IR sensor."""

    def make(addr):
        from ev3dev2.sensor.lego import InfraredSensor

        return InfraredSensor(addr)

    sensor = _sensor("infrared", make)
    return None if sensor is None else sensor.proximity


def obstacle_cm():
    """Distance ahead in cm; approximate when derived from IR. None if neither sensor."""
    exact = distance_cm()
    if exact is not None:
        return exact
    prox = proximity()
    if prox is None:
        return None
    # IR proximity is a 0-100 percentage of roughly 70cm of usable range.
    return prox * 0.7


def touch_pressed():
    def make(addr):
        from ev3dev2.sensor.lego import TouchSensor

        return TouchSensor(addr)

    sensor = _sensor("touch", make)
    return None if sensor is None else bool(sensor.is_pressed)


def color():
    """Detected colour name (e.g. 'Red'), or None when no colour sensor is attached."""

    def make(addr):
        from ev3dev2.sensor.lego import ColorSensor

        return ColorSensor(addr)

    sensor = _sensor("color", make)
    return None if sensor is None else sensor.color_name


def reflected_light():
    def make(addr):
        from ev3dev2.sensor.lego import ColorSensor

        return ColorSensor(addr)

    sensor = _sensor("color", make)
    return None if sensor is None else sensor.reflected_light_intensity


def gyro_angle():
    def make(addr):
        from ev3dev2.sensor.lego import GyroSensor

        return GyroSensor(addr)

    sensor = _sensor("gyro", make)
    return None if sensor is None else sensor.angle


def wheel_degrees():
    """(left, right) encoder positions -- the basis for dead reckoning."""
    tank = _tank()
    return (tank.left_motor.position, tank.right_motor.position)


def reset_wheels():
    tank = _tank()
    tank.left_motor.position = 0
    tank.right_motor.position = 0


def beep():
    from ev3dev2.sound import Sound

    Sound().beep()


def say(text):
    from ev3dev2.sound import Sound

    Sound().speak(str(text))


def devices():
    """Inventory of attached motors and sensors."""
    from ev3dev2.motor import list_motors
    from ev3dev2.sensor import list_sensors

    motors = []
    for motor in list_motors():
        motors.append({"address": motor.address, "driver": motor.driver_name})

    sensors = []
    for sensor in list_sensors():
        sensors.append(
            {
                "address": sensor.address,
                "driver": sensor.driver_name,
                "mode": sensor.mode,
            }
        )
    return {"motors": motors, "sensors": sensors}
