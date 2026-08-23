"""Configuration from environment variables, with an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "trading.db"


def load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a .env file without pulling in a dependency.

    Values already present in the environment win, so a shell export still
    overrides the file.
    """
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    db_path: Path
    host: str
    port: int
    api_token: Optional[str]
    mt5_login: Optional[int]
    mt5_password: Optional[str]
    mt5_server: Optional[str]
    mt5_path: Optional[str]
    mt5_history_days: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        login = os.environ.get("MT5_LOGIN") or ""
        return cls(
            db_path=Path(os.environ.get("TRADINGAPP_DB") or DEFAULT_DB),
            # Loopback by default: this server exposes account history, so it should
            # not answer on the network unless the user opts in.
            host=os.environ.get("TRADINGAPP_HOST", "127.0.0.1"),
            port=_int("TRADINGAPP_PORT", 8420),
            api_token=os.environ.get("TRADINGAPP_TOKEN") or None,
            mt5_login=int(login) if login.isdigit() else None,
            mt5_password=os.environ.get("MT5_PASSWORD") or None,
            mt5_server=os.environ.get("MT5_SERVER") or None,
            mt5_path=os.environ.get("MT5_PATH") or None,
            mt5_history_days=_int("MT5_HISTORY_DAYS", 3650),
        )
