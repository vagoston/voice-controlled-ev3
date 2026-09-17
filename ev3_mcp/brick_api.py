"""Brick-side API that skills are written against.

Runs on the EV3 under MicroPython (ev3dev), never imported on the host -- the
SSH runner ships it as text. Target Python 3.5 / MicroPython: no f-strings, no
modern typing syntax.

The host prepends _MAX_SPEED, _MAX_DURATION, _LEFT_PORT and _RIGHT_PORT before
this source, so those names already exist when this executes.
"""

import time

_devices = {}


def _clamp(value, low, high):
    return max(low, min(value, high))


def _port(letter):
    from ev3dev2.motor import OUTPUT_A, OUTPUT_B, OUTPUT_C, OUTPUT_D

    mapping = {"A": OUTPUT_A, "B": OUTPUT_B, "C": OUTPUT_C, "D": OUTPUT_D}
    key = str(letter).upper()
    if key not in mapping:
        raise ValueError("Invalid motor port: {!r}".format(letter))
    return mapping[key]


def _tank():
    if "tank" not in _devices:
        from ev3dev2.motor import MoveTank

        _devices["tank"] = MoveTank(_port(_LEFT_PORT), _port(_RIGHT_PORT))
    return _devices["tank"]


def _sensor(key, factory):
    """Instantiate a sensor once; None (cached) when it isn't plugged in."""
    if key not in _devices:
        try:
            _devices[key] = factory()
        except Exception:
            _devices[key] = None
    return _devices[key]


def log(message):
    print(message)


def sleep(seconds):
    time.sleep(_clamp(float(seconds), 0.0, _MAX_DURATION))


def drive(left_pct, right_pct, seconds=None):
    """Run both drive motors. Blocks for `seconds`, then stops; else returns immediately."""
    from ev3dev2.motor import SpeedPercent

    left = _clamp(float(left_pct), -_MAX_SPEED, _MAX_SPEED)
    right = _clamp(float(right_pct), -_MAX_SPEED, _MAX_SPEED)
    tank = _tank()
    if seconds is None:
        tank.on(SpeedPercent(left), SpeedPercent(right))
    else:
        tank.on_for_seconds(
            SpeedPercent(left),
            SpeedPercent(right),
            _clamp(float(seconds), 0.0, _MAX_DURATION),
        )


def forward(speed_pct=40, seconds=1.0):
    drive(speed_pct, speed_pct, seconds)


def backward(speed_pct=40, seconds=1.0):
    drive(-speed_pct, -speed_pct, seconds)


def spin_left(speed_pct=30, seconds=0.5):
    drive(-speed_pct, speed_pct, seconds)


def spin_right(speed_pct=30, seconds=0.5):
    drive(speed_pct, -speed_pct, seconds)


def stop():
    _tank().off(brake=True)


def distance_cm():
    """Ultrasonic distance, or None when no ultrasonic sensor is attached."""

    def make():
        from ev3dev2.sensor.lego import UltrasonicSensor

        return UltrasonicSensor()

    sensor = _sensor("ultrasonic", make)
    return None if sensor is None else sensor.distance_centimeters


def touch_pressed():
    def make():
        from ev3dev2.sensor.lego import TouchSensor

        return TouchSensor()

    sensor = _sensor("touch", make)
    return None if sensor is None else bool(sensor.is_pressed)


def color():
    """Detected colour name (e.g. 'Red'), or None when no colour sensor is attached."""

    def make():
        from ev3dev2.sensor.lego import ColorSensor

        return ColorSensor()

    sensor = _sensor("color", make)
    return None if sensor is None else sensor.color_name


def reflected_light():
    def make():
        from ev3dev2.sensor.lego import ColorSensor

        return ColorSensor()

    sensor = _sensor("color", make)
    return None if sensor is None else sensor.reflected_light_intensity


def gyro_angle():
    def make():
        from ev3dev2.sensor.lego import GyroSensor

        return GyroSensor()

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
