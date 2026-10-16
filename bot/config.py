import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


class ConfigError(RuntimeError):
    pass


def _load_dotenv(path: Path) -> None:
    # Locally there is no Docker env_file, so read KEY=VALUE pairs ourselves;
    # real environment variables always win.
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_id: int
    db_path: Path
    # e.g. socks5://user:pass@host:port or http://host:port; None means direct connection
    proxy_url: str | None = None
    # Baked into the Docker image by CI; empty for local runs
    app_version: str | None = None
    app_title: str | None = None
    # Set by deploy.sh when it rolled back from this version
    failed_version: str | None = None
    # Read-only HTTP API for other services; disabled while the token is empty
    api_token: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    # Telegram id of the user whose data the API serves
    api_telegram_id: int | None = None

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def db_readonly_url(self) -> str:
        # SQLite URI mode: the API's connections physically cannot write
        path = self.db_path.resolve().as_posix()
        if not path.startswith("/"):
            path = "/" + path  # Windows drive letter: file:/C:/...
        return f"sqlite+aiosqlite:///file:{quote(path, safe='/:')}?mode=ro&uri=true"


def load_config() -> Config:
    _load_dotenv(Path(".env"))
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN is not set")
    raw_user_id = os.environ.get("ALLOWED_USER_ID", "").strip()
    try:
        allowed_user_id = int(raw_user_id)
    except ValueError:
        raise ConfigError("ALLOWED_USER_ID must be a numeric Telegram user id") from None
    db_path = Path(os.environ.get("DB_PATH", "data/subs.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    proxy_url = os.environ.get("PROXY_URL", "").strip() or None
    api_port = _int("API_PORT", 8080)
    if not 0 < api_port < 65536:
        raise ConfigError("API_PORT must be between 1 and 65535")
    return Config(
        bot_token=token,
        allowed_user_id=allowed_user_id,
        db_path=db_path,
        proxy_url=proxy_url,
        app_version=_optional("APP_VERSION"),
        app_title=_optional("APP_TITLE"),
        failed_version=_optional("FAILED_VERSION"),
        api_token=_optional("API_TOKEN"),
        api_host=_optional("API_HOST") or "127.0.0.1",
        api_port=api_port,
        api_telegram_id=_int("API_TELEGRAM_ID", allowed_user_id),
    )


def _optional(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def _int(name: str, default: int) -> int:
    raw = _optional(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
