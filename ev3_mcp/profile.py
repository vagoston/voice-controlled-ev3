"""Loads the hardware profile: what is attached, where, and what to know about it.

Every motor is named. `left` and `right` are not special in the schema; they are
simply the names `drive()` looks for, so a robot without them is valid and only
fails when something tries to drive.

Notes are fields rather than TOML comments because the profile is fed to the
model. A comment would be invisible to it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

MOTOR_PORTS = {"A", "B", "C", "D"}
SENSOR_INPUTS = {1, 2, 3, 4}
KNOWN_SENSORS = {"touch", "color", "infrared", "ultrasonic", "gyro", "sound"}
MOTOR_KINDS = {"large", "medium", None}


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class Motor:
    name: str
    port: str
    kind: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Sensor:
    kind: str
    input: int
    note: str | None = None


@dataclass(frozen=True)
class Geometry:
    wheel_diameter_mm: float | None = None
    axle_track_mm: float | None = None

    @property
    def complete(self) -> bool:
        return self.wheel_diameter_mm is not None and self.axle_track_mm is not None


@dataclass(frozen=True)
class RobotProfile:
    name: str
    motors: dict[str, Motor]
    sensors: dict[str, Sensor]
    geometry: Geometry
    note: str | None = None

    def motor_port(self, ref: str) -> str:
        key = str(ref).strip().lower()
        if key in self.motors:
            return self.motors[key].port
        upper = str(ref).strip().upper()
        if upper in MOTOR_PORTS:
            return upper
        raise ProfileError(
            f"Unknown motor {ref!r}. Named motors: {sorted(self.motors)}; ports: A-D"
        )

    @property
    def has_drive(self) -> bool:
        return "left" in self.motors and "right" in self.motors

    def as_dict(self) -> dict:
        """The shape shipped to the brick. Notes stay on the host."""
        return {
            "name": self.name,
            "motors": {name: m.port for name, m in self.motors.items()},
            "sensors": {s.kind: s.input for s in self.sensors.values()},
            "geometry": {
                "wheel_diameter_mm": self.geometry.wheel_diameter_mm,
                "axle_track_mm": self.geometry.axle_track_mm,
            },
        }

    def summary(self) -> str:
        motors = ", ".join(f"{m.port}={name}" for name, m in sorted(self.motors.items()))
        sensors = ", ".join(
            f"in{s.input}={s.kind}" for s in sorted(self.sensors.values(), key=lambda s: s.input)
        )
        return f"{self.name}: motors {motors or 'none'}; sensors {sensors or 'none'}"

    def describe(self) -> str:
        """Multi-line description handed to the model, notes included."""
        lines = [f"This robot ({self.name}) has:"]
        if self.note:
            lines.append(f"  {self.note}")

        lines.append("  motors:")
        for name, motor in sorted(self.motors.items()):
            kind = f" {motor.kind}" if motor.kind else ""
            lines.append(f"    {name} on port {motor.port}{kind}")
            if motor.note:
                lines.append(f"      note: {motor.note}")

        lines.append("  sensors:")
        for sensor in sorted(self.sensors.values(), key=lambda s: s.input):
            lines.append(f"    {sensor.kind} on input {sensor.input}")
            if sensor.note:
                lines.append(f"      note: {sensor.note}")

        missing = sorted(KNOWN_SENSORS - set(self.sensors))
        if missing:
            lines.append(f"  not fitted: {', '.join(missing)} (those helpers return None)")

        if self.geometry.complete:
            lines.append(
                f"  geometry: {self.geometry.wheel_diameter_mm:g}mm wheels, "
                f"{self.geometry.axle_track_mm:g}mm apart"
            )
        else:
            lines.append(
                "  geometry: not measured, so distances and turn angles cannot be "
                "computed. Use durations and report that they are approximate."
            )

        if not self.has_drive:
            lines.append("  no left/right drive motors, so drive() will not work")
        return "\n".join(lines)


def _parse_motor(name: str, value: object, path: Path) -> Motor:
    if isinstance(value, str):
        value = {"port": value}
    if not isinstance(value, dict):
        raise ProfileError(f"{path}: motor {name!r} must be a port letter or a table")

    port = str(value.get("port", "")).upper()
    if port not in MOTOR_PORTS:
        raise ProfileError(f"{path}: motor {name!r} has invalid port {value.get('port')!r}")

    kind = value.get("kind")
    if kind is not None:
        kind = str(kind).lower()
    if kind not in MOTOR_KINDS:
        raise ProfileError(f"{path}: motor {name!r} has unknown kind {kind!r}")

    note = value.get("note")
    return Motor(name=name, port=port, kind=kind, note=str(note) if note else None)


def _parse_sensor(kind: str, value: object, path: Path) -> Sensor:
    if isinstance(value, int):
        value = {"input": value}
    if not isinstance(value, dict):
        raise ProfileError(f"{path}: sensor {kind!r} must be an input number or a table")

    if kind not in KNOWN_SENSORS:
        raise ProfileError(
            f"{path}: unknown sensor {kind!r}; expected one of {sorted(KNOWN_SENSORS)}"
        )

    number = value.get("input")
    if number not in SENSOR_INPUTS:
        raise ProfileError(f"{path}: sensor {kind!r} has invalid input {number!r}")

    note = value.get("note")
    return Sensor(kind=kind, input=int(number), note=str(note) if note else None)


def _parse_geometry(raw: dict, path: Path) -> Geometry:
    values = {}
    for field in ("wheel_diameter_mm", "axle_track_mm"):
        value = raw.get(field)
        if value is None:
            values[field] = None
            continue
        if not isinstance(value, (int, float)) or value <= 0:
            raise ProfileError(f"{path}: [geometry] {field} must be a positive number")
        values[field] = float(value)
    return Geometry(**values)


def load_profile(path: Path) -> RobotProfile:
    if not path.is_file():
        raise ProfileError(f"No hardware profile at {path}")

    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    motors: dict[str, Motor] = {}
    for name, value in (raw.get("motors") or {}).items():
        motors[name.lower()] = _parse_motor(name.lower(), value, path)

    used_ports = [m.port for m in motors.values()]
    for port in used_ports:
        if used_ports.count(port) > 1:
            names = sorted(n for n, m in motors.items() if m.port == port)
            raise ProfileError(f"{path}: motors {names} share port {port}")

    sensors: dict[str, Sensor] = {}
    for kind, value in (raw.get("sensors") or {}).items():
        sensors[kind.lower()] = _parse_sensor(kind.lower(), value, path)

    used_inputs = [s.input for s in sensors.values()]
    for number in used_inputs:
        if used_inputs.count(number) > 1:
            kinds = sorted(s.kind for s in sensors.values() if s.input == number)
            raise ProfileError(f"{path}: sensors {kinds} share input {number}")

    note = raw.get("note")
    return RobotProfile(
        name=str(raw.get("name", "robot")),
        motors=motors,
        sensors=sensors,
        geometry=_parse_geometry(raw.get("geometry") or {}, path),
        note=str(note) if note else None,
    )
