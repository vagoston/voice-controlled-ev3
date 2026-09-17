import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    host: str
    user: str
    password: str | None
    ssh_key_path: Path
    dry_run: bool
    python_bin: str
    left_motor: str
    right_motor: str
    max_speed: int
    max_duration: float
    default_timeout: float
    max_timeout: float
    skills_dir: Path

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

    return Settings(
        host=os.getenv("EV3_HOST", "192.168.1.100").strip(),
        user=os.getenv("EV3_USER", "robot").strip(),
        password=password,
        ssh_key_path=Path(key_path).expanduser(),
        dry_run=_env_bool("EV3_DRY_RUN", True),
        python_bin=os.getenv("EV3_PYTHON_BIN", "micropython").strip() or "micropython",
        left_motor=os.getenv("EV3_LEFT_MOTOR", "B").strip().upper(),
        right_motor=os.getenv("EV3_RIGHT_MOTOR", "C").strip().upper(),
        max_speed=_env_int("EV3_MAX_SPEED", 80),
        max_duration=_env_float("EV3_MAX_DURATION", 10.0),
        default_timeout=_env_float("EV3_DEFAULT_TIMEOUT", 20.0),
        max_timeout=_env_float("EV3_MAX_TIMEOUT", 60.0),
        skills_dir=resolved_skills,
    )
