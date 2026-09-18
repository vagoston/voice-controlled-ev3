import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ev3_mcp.profile import RobotProfile, load_profile

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    host: str
    user: str
    password: str | None
    ssh_key_path: Path
    dry_run: bool
    python_bin: str
    profile: RobotProfile
    max_duration: float
    default_timeout: float
    max_timeout: float
    skills_dir: Path

    @property
    def left_motor(self) -> str:
        return self.profile.left_motor

    @property
    def right_motor(self) -> str:
        return self.profile.right_motor

    def clamp_timeout(self, timeout_s: float | None) -> float:
        value = self.default_timeout if timeout_s is None else float(timeout_s)
        return max(1.0, min(value, self.max_timeout))


def load_settings() -> Settings:
    password = os.getenv("EV3_PASSWORD")
    if password is not None and not password.strip():
        password = None

    skills_dir = os.getenv("EV3_SKILLS_DIR")
    resolved_skills = Path(skills_dir).expanduser() if skills_dir else PROJECT_ROOT / "skills"

    key_path = os.getenv("EV3_SSH_KEY_PATH") or "~/.ssh/id_ev3"

    profile_path = os.getenv("EV3_PROFILE")
    profile = load_profile(
        Path(profile_path).expanduser() if profile_path else PROJECT_ROOT / "robot.toml"
    )

    return Settings(
        host=os.getenv("EV3_HOST", "192.168.1.100").strip(),
        user=os.getenv("EV3_USER", "robot").strip(),
        password=password,
        ssh_key_path=Path(key_path).expanduser(),
        dry_run=_env_bool("EV3_DRY_RUN", True),
        python_bin=os.getenv("EV3_PYTHON_BIN", "micropython").strip() or "micropython",
        profile=profile,
        # Loose sanity bounds against typos, not policy. The runtime budget is
        # what actually bounds a run.
        max_duration=_env_float("EV3_MAX_DURATION", 120.0),
        default_timeout=_env_float("EV3_DEFAULT_TIMEOUT", 30.0),
        max_timeout=_env_float("EV3_MAX_TIMEOUT", 600.0),
        skills_dir=resolved_skills,
    )
