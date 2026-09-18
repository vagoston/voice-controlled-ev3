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
_LEFT_PORT = _MOTORS.get("left")
_RIGHT_PORT = _MOTORS.get("right")


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

    left = SpeedPercent(_speed(left_pct))
    right = SpeedPercent(_speed(right_pct))
    if seconds is None:
        tank.on(left, right)
    else:
        tank.on_for_seconds(left, right, _bounded(seconds))


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
        return _MOTORS[key]
    return str(ref).upper()


def motor_roles():
    """Names this robot's motors answer to, from the profile."""
    return sorted(_MOTORS.keys())


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
