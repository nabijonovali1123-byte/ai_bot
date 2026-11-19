from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import dotenv_values

SETTING_KEYS = (
    "TELEGRAM_BOT_TOKEN", "OWNER_ID", "AUTO_SEND", "CODEX_MODEL",
    "CODEX_CREDENTIALS_JSON", "PROFILE_CONTENT", "CODEX_TIMEOUT_SECONDS",
    "LIMIT_COOLDOWN_MINUTES", "OWNER_TAKEOVER_MINUTES", "HISTORY_LIMIT",
    "DATA_DIR", "PROFILE_PATH",
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: Optional[str] = field(default=None, repr=False)
    owner_id: Optional[int] = None
    auto_send: bool = False
    codex_model: Optional[str] = None
    codex_credentials_json: Optional[str] = field(default=None, repr=False)
    profile_content: Optional[str] = field(default=None, repr=False)
    data_dir: Path = Path("data")
    profile_path: Path = Path("profile.md")
    timeout_seconds: int = 120
    limit_cooldown_minutes: int = 30
    owner_takeover_minutes: int = 15
    history_limit: int = 30
    from_environment: bool = False

    @property
    def codex_auth_mode(self) -> str:
        return "ChatGPT plan OAuth"


def _boolean(name: str, value: Optional[str], default: bool, location: str) -> bool:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    raise ConfigError("Invalid {} in {}. Use true/false, 1/0, yes/no, or on/off.".format(name, location))


def _integer(name: str, value: Optional[str], default: int, minimum: int, location: str) -> int:
    if value is None or value == "":
        return default
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ConfigError("Invalid {} in {}. Use a whole number.".format(name, location))
    if result < minimum:
        raise ConfigError("{} in {} must be at least {}.".format(name, location, minimum))
    return result


def load_settings(
    env_file: Path = Path(".env"), *, require_telegram: bool = True,
    overrides: Optional[Mapping[str, str]] = None,
    source: Optional[Mapping[str, str]] = None,
) -> Settings:
    path = Path(env_file)
    location = "the environment variables" if source is not None else str(path.resolve())
    if source is not None:
        values = dict(source)
    else:
        if not path.exists():
            raise ConfigError("Settings file is missing at {}. Create it with: cp .env.example .env && chmod 600 .env".format(location))
        values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    if overrides:
        values.update(overrides)

    token = values.get("TELEGRAM_BOT_TOKEN") or None
    owner_text = values.get("OWNER_ID") or None
    if require_telegram:
        if not token:
            raise ConfigError("TELEGRAM_BOT_TOKEN is required in {}.".format(location))
        if not re.fullmatch(r"\d+:.+", token):
            raise ConfigError("TELEGRAM_BOT_TOKEN has an invalid format in {}.".format(location))
        if not owner_text or not owner_text.isdigit():
            raise ConfigError("OWNER_ID must contain digits only in {}.".format(location))
    owner_id = int(owner_text) if owner_text and owner_text.isdigit() else None
    return Settings(
        bot_token=token,
        owner_id=owner_id,
        auto_send=_boolean("AUTO_SEND", values.get("AUTO_SEND"), False, location),
        codex_model=values.get("CODEX_MODEL") or None,
        codex_credentials_json=values.get("CODEX_CREDENTIALS_JSON") or None,
        profile_content=values.get("PROFILE_CONTENT") or None,
        data_dir=Path(values.get("DATA_DIR") or ("/app/data" if source is not None else "data")),
        profile_path=Path(values.get("PROFILE_PATH") or ("/app/data/profile.md" if source is not None else "profile.md")),
        timeout_seconds=_integer("CODEX_TIMEOUT_SECONDS", values.get("CODEX_TIMEOUT_SECONDS"), 120, 1, location),
        limit_cooldown_minutes=_integer("LIMIT_COOLDOWN_MINUTES", values.get("LIMIT_COOLDOWN_MINUTES"), 30, 1, location),
        owner_takeover_minutes=_integer("OWNER_TAKEOVER_MINUTES", values.get("OWNER_TAKEOVER_MINUTES"), 15, 0, location),
        history_limit=_integer("HISTORY_LIMIT", values.get("HISTORY_LIMIT"), 30, 1, location),
        from_environment=source is not None,
    )


def settings_from_process_env(*, require_telegram: bool = True) -> Settings:
    values = {key: os.environ[key] for key in SETTING_KEYS if key in os.environ}
    try:
        return load_settings(require_telegram=require_telegram, source=values)
    finally:
        for key in values:
            os.environ.pop(key, None)


def env_file_warning(path: Path) -> Optional[str]:
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return None
    if mode & 0o077:
        return "Settings file permissions are too open; run chmod 600 {}.".format(path)
    return None


def resolve_binary(name: str) -> Optional[str]:
    return shutil.which(name)
