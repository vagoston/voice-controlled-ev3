"""Run a saved skill directly against the robot, bypassing the voice agent.

Usage: run_skill.py <skill> [key=value ...] [--timeout SECONDS]

Arguments are key=value rather than JSON because PowerShell strips double
quotes when passing to a native executable, which mangles JSON on the way in.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ev3_mcp.skills import SkillStore  # noqa: E402
from ev3_mcp.ssh_runner import get_runner  # noqa: E402


def _value(text: str):
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("none", "null"):
        return None
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def _parse_args(pairs: list[str]) -> dict:
    parsed = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"Expected key=value, got {pair!r}")
        key, _, raw = pair.partition("=")
        parsed[key.strip()] = _value(raw)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("skill")
    parser.add_argument("args", nargs="*", help="key=value pairs, e.g. cm=20 degrees=360")
    parser.add_argument("--timeout", type=float, default=None)
    opts = parser.parse_args()

    runner = get_runner()
    store = SkillStore(runner.settings.skills_dir)
    if not store.exists(opts.skill):
        print(f"No skill named {opts.skill!r}. Available: {', '.join(store.names())}")
        raise SystemExit(1)

    call_args = _parse_args(opts.args)
    body = f"{store.combined_source()}\n\n{opts.skill}(**{call_args!r})"

    print(f"running {opts.skill}({call_args}) against {runner.settings.host}", flush=True)
    result = runner.run_source(body, opts.timeout)

    print(result["stdout"], end="")
    if result.get("stderr"):
        print("--- stderr ---", file=sys.stderr)
        print(result["stderr"], file=sys.stderr)
    if not result["ok"]:
        print(f"FAILED: {result.get('error')}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
