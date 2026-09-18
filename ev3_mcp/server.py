"""FastMCP stdio server: a programmable EV3 the model writes code for."""

from __future__ import annotations

import ast
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ev3_mcp.skills import SkillError, SkillStore
from ev3_mcp.ssh_runner import BRICK_API_PATH, RobotBusyError, get_runner

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ev3_mcp")

mcp = FastMCP("EV3Programmable", log_level="ERROR")

_store: SkillStore | None = None


def get_store() -> SkillStore:
    global _store
    if _store is None:
        _store = SkillStore(get_runner().settings.skills_dir)
    return _store


def _api_summary() -> str:
    """Signatures of the brick API, derived from the source so it cannot drift."""
    tree = ast.parse(BRICK_API_PATH.read_text(encoding="utf-8"))
    lines = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        doc = ast.get_docstring(node) or ""
        first = doc.strip().splitlines()[0] if doc else ""
        signature = f"{node.name}({', '.join(a.arg for a in node.args.args)})"
        lines.append(f"  {signature}" + (f"  # {first}" if first else ""))
    return "\n".join(lines)


def _hardware_summary() -> str:
    """What is actually plugged in, so the model need not guess or probe."""
    profile = get_runner().settings.profile
    motors = [f"  {profile.left_motor} = left drive", f"  {profile.right_motor} = right drive"]
    motors += [f"  {port} = {role}" for role, port in sorted(profile.named_motors.items())]
    sensors = [f"  in{n} = {kind}" for kind, n in sorted(profile.sensors.items(), key=lambda kv: kv[1])]
    missing = sorted({"ultrasonic", "gyro"} - set(profile.sensors))
    lines = [f"This robot ({profile.name}) has:", *motors, *sensors]
    if missing:
        lines.append(
            "  NOT fitted: " + ", ".join(missing) + " -- helpers for these return None."
        )
    return "\n".join(lines)


CODE_RULES = f"""
Code runs ON the EV3 under MicroPython (Python 3.5): no f-strings, no modern
typing syntax. Use .format() for strings.

{_hardware_summary()}

Address motors by role, not port letter: motor("head", 30, 0.5).

These helpers are already defined -- do not import ev3dev2 yourself:
{_api_summary()}

Sensor helpers return None when that sensor is not attached; check before use.

Every run has a time budget. Poll out_of_time() in long loops and exit cleanly
so your partial results still come back -- if you overrun it, the host kills the
run and you report nothing. Durations are clamped to the remaining budget.
Motors are always stopped when a run ends, so continuous motion has to happen
inside one program.

Anything you print() comes back to you as stdout, so print what you want to
report.
""".strip()


def _run_body(body: str, timeout_s: float | None) -> dict[str, Any]:
    runner = get_runner()
    combined = get_store().combined_source()
    full = f"{combined}\n\n{body}" if combined else body
    try:
        return runner.run_source(full, timeout_s)
    except RobotBusyError as exc:
        return {"ok": False, "busy": True, "error": str(exc)}


@mcp.tool(
    name="run_python",
    description=(
        "Run one-off Python on the robot and get its printed output back. Use for "
        "exploration and experiments you do not want to keep. Saved skills are also "
        "in scope, so you can call them.\n\n" + CODE_RULES
    ),
)
def run_python(source: str, timeout_s: float | None = None) -> dict[str, Any]:
    return _run_body(source, timeout_s)


@mcp.tool(
    name="define_skill",
    description=(
        "Save a reusable skill to the durable library. The source MUST define a "
        "function whose name matches `name` -- that is its entry point. Skills may "
        "call other saved skills. Set overwrite=true to replace an existing skill "
        "(call get_skill first to see what you are replacing).\n\n" + CODE_RULES
    ),
)
def define_skill(
    name: str,
    source: str,
    description: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    try:
        path = get_store().save(name, source, description, overwrite=overwrite)
    except SkillError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": name, "path": str(path)}


@mcp.tool(
    name="run_skill",
    description=(
        "Run a saved skill by name, passing its arguments as a JSON object. "
        "Returns whatever the skill printed."
    ),
)
def run_skill(
    name: str,
    args: dict[str, Any] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    store = get_store()
    if not store.exists(name):
        return {"ok": False, "error": f"No skill named {name!r}. Call list_skills."}
    call_args = args or {}
    result = _run_body(f"{name}(**{call_args!r})", timeout_s)
    result["skill"] = name
    return result


@mcp.tool(
    name="list_skills",
    description="List every saved skill with its description.",
)
def list_skills() -> dict[str, Any]:
    return {"ok": True, "skills": get_store().list()}


@mcp.tool(
    name="get_skill",
    description="Read a saved skill's full source. Do this before overwriting one.",
)
def get_skill(name: str) -> dict[str, Any]:
    try:
        return {"ok": True, "name": name, "source": get_store().get(name)}
    except SkillError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    name="delete_skill",
    description="Permanently remove a saved skill from the library.",
)
def delete_skill(name: str) -> dict[str, Any]:
    try:
        get_store().delete(name)
    except SkillError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "deleted": name}


@mcp.tool(
    name="stop",
    description="Immediately cut power to the drive motors. Use this the moment anything looks wrong.",
)
def stop() -> dict[str, Any]:
    return get_runner().run_source("stop()", timeout_s=10, bypass_lock=True)


@mcp.tool(
    name="list_devices",
    description=(
        "List what is actually plugged into the EV3 and check it against the "
        "configured hardware profile. Use this when something behaves as if a "
        "motor or sensor is missing."
    ),
)
def list_devices() -> dict[str, Any]:
    result = _run_body("import json; print(json.dumps(devices()))", timeout_s=15)
    raw = (result.get("stdout") or "").strip()
    if not result.get("ok") or not raw:
        return result
    try:
        found = json.loads(raw)
    except json.JSONDecodeError:
        return result

    profile = get_runner().settings.profile
    actual_motors = {m["address"].rsplit(":out", 1)[-1] for m in found.get("motors", [])}
    actual_sensors = {s["address"].rsplit(":in", 1)[-1] for s in found.get("sensors", [])}

    problems = []
    for role, port in [
        ("left drive", profile.left_motor),
        ("right drive", profile.right_motor),
        *sorted(profile.named_motors.items()),
    ]:
        if port not in actual_motors:
            problems.append(f"profile expects {role} on port {port}, nothing is there")
    for kind, number in sorted(profile.sensors.items()):
        if str(number) not in actual_sensors:
            problems.append(f"profile expects {kind} on input {number}, nothing is there")

    result["devices"] = found
    result["profile"] = profile.summary()
    result["matches_profile"] = not problems
    result["problems"] = problems
    return result


@mcp.tool(
    name="robot_status",
    description="Report connection state, dry-run flag, motor ports, safety caps, and last error.",
)
def robot_status() -> dict[str, Any]:
    return get_runner().status()


def main() -> None:
    runner = get_runner()
    if runner.settings.dry_run:
        logger.info("EV3_DRY_RUN=1 - no SSH; tools return the program that would run")
    else:
        try:
            runner.connect()
            logger.info("SSH connected to %s@%s", runner.settings.user, runner.settings.host)
        except Exception as exc:
            logger.error("SSH connect failed (tools will retry): %s", exc)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
