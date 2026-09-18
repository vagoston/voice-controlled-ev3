"""Loads the hardware profile: which motor and sensor is on which port.

One file describes the robot, so rebuilding it means editing `robot.toml`
rather than hunting through code, env vars and documentation.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MOTOR_PORTS = {"A", "B", "C", "D"}
SENSOR_INPUTS = {1, 2, 3, 4}
KNOWN_SENSORS = {"touch", "color", "infrared", "ultrasonic", "gyro", "sound"}


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class RobotProfile:
    name: str
    left_motor: str
    right_motor: str
    named_motors: dict[str, str] = field(default_factory=dict)
    sensors: dict[str, int] = field(default_factory=dict)

    def motor_port(self, ref: str) -> str:
        """Resolve a role name (``head``) or a bare port letter (``A``)."""
        key = str(ref).strip()
        if key.lower() in self.named_motors:
            return self.named_motors[key.lower()]
        if key.lower() == "left":
            return self.left_motor
        if key.lower() == "right":
            return self.right_motor
        upper = key.upper()
        if upper in MOTOR_PORTS:
            return upper
        raise ProfileError(
            f"Unknown motor {ref!r}. Roles: {sorted(self.roles())}; ports: A-D"
        )

    def roles(self) -> set[str]:
        return {"left", "right", *self.named_motors}

    def as_dict(self) -> dict:
        """The shape shipped to the brick and injected into the preamble."""
        return {
            "name": self.name,
            "left": self.left_motor,
            "right": self.right_motor,
            "named": dict(self.named_motors),
            "sensors": dict(self.sensors),
        }

    def summary(self) -> str:
        motors = [f"{self.left_motor}=left", f"{self.right_motor}=right"]
        motors += [f"{port}={role}" for role, port in sorted(self.named_motors.items())]
        sensors = [f"in{n}={kind}" for kind, n in sorted(self.sensors.items(), key=lambda kv: kv[1])]
        return f"{self.name}: motors {', '.join(motors)}; sensors {', '.join(sensors) or 'none'}"


def load_profile(path: Path) -> RobotProfile:
    if not path.is_file():
        raise ProfileError(f"No hardware profile at {path}")

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    motors = raw.get("motors", {})
    left = str(motors.get("left", "")).upper()
    right = str(motors.get("right", "")).upper()
    if left not in MOTOR_PORTS or right not in MOTOR_PORTS:
        raise ProfileError(f"{path}: [motors] left and right must be ports A-D")
    if left == right:
        raise ProfileError(f"{path}: left and right drive motors share port {left}")

    named = {}
    for role, port in (motors.get("named") or {}).items():
        letter = str(port).upper()
        if letter not in MOTOR_PORTS:
            raise ProfileError(f"{path}: motor {role!r} has invalid port {port!r}")
        if letter in (left, right):
            raise ProfileError(f"{path}: motor {role!r} reuses drive port {letter}")
        named[role.lower()] = letter

    sensors = {}
    for kind, number in (raw.get("sensors") or {}).items():
        if kind.lower() not in KNOWN_SENSORS:
            raise ProfileError(
                f"{path}: unknown sensor {kind!r}; expected one of {sorted(KNOWN_SENSORS)}"
            )
        if number not in SENSOR_INPUTS:
            raise ProfileError(f"{path}: sensor {kind!r} has invalid input {number!r}")
        sensors[kind.lower()] = int(number)

    duplicates = [n for n in sensors.values() if list(sensors.values()).count(n) > 1]
    if duplicates:
        raise ProfileError(f"{path}: two sensors share input {duplicates[0]}")

    return RobotProfile(
        name=str(raw.get("name", "robot")),
        left_motor=left,
        right_motor=right,
        named_motors=named,
        sensors=sensors,
    )
