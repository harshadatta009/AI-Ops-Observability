import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    prometheus_url: str
    loki_url: str
    groq_api_key: str
    groq_model: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_to: str
    smtp_use_tls: bool
    lookback_minutes: int
    max_log_lines: int
    max_log_chars: int
    brand_icon_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(BASE_DIR / ".env")
        return cls(
            prometheus_url=os.getenv("PROMETHEUS_URL", "http://10.100.0.142:9090").rstrip("/"),
            loki_url=os.getenv("LOKI_URL", "http://10.100.0.142:3100").rstrip("/"),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            groq_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=_get_int("SMTP_PORT", 587),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", "ai-observability@local"),
            smtp_to=os.getenv("SMTP_TO", ""),
            smtp_use_tls=_get_bool("SMTP_USE_TLS", True),
            lookback_minutes=_get_int("LOOKBACK_MINUTES", 60),
            max_log_lines=_get_int("MAX_LOG_LINES", 50),
            max_log_chars=_get_int("MAX_LOG_CHARS", 3000),
            brand_icon_path=BASE_DIR / "assets" / "varsapradaya-icon.webp",
        )

