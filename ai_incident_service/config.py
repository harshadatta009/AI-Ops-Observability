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


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


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
    # Security: shared secrets for endpoint authentication (empty = open + warn).
    webhook_token: str
    reports_api_key: str
    # Operational logging.
    log_level: str
    log_format: str  # "json" or "text"
    # Resilience: timeouts and retries for outbound calls.
    http_timeout_seconds: int
    http_max_retries: int
    groq_timeout_seconds: int
    groq_max_retries: int
    # Deep incident analysis windows and thresholds.
    incident_window_minutes: int
    pre_window_minutes: int
    post_window_minutes: int
    baseline_window_minutes: int
    baseline_offset_minutes: int
    analysis_step_seconds: int
    anomaly_confirm_ratio: float
    anomaly_probable_ratio: float
    anomaly_possible_ratio: float
    min_evidence_score: float

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
            webhook_token=os.getenv("WEBHOOK_TOKEN", ""),
            reports_api_key=os.getenv("REPORTS_API_KEY", ""),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_format=os.getenv("LOG_FORMAT", "json").lower(),
            http_timeout_seconds=_get_int("HTTP_TIMEOUT_SECONDS", 15),
            http_max_retries=_get_int("HTTP_MAX_RETRIES", 3),
            groq_timeout_seconds=_get_int("GROQ_TIMEOUT_SECONDS", 45),
            groq_max_retries=_get_int("GROQ_MAX_RETRIES", 3),
            # "During" window straddles the alert start; pre/post bracket it so we
            # can see behaviour before, during, and after the suspected incident.
            incident_window_minutes=_get_int("INCIDENT_WINDOW_MINUTES", 30),
            pre_window_minutes=_get_int("PRE_WINDOW_MINUTES", 30),
            post_window_minutes=_get_int("POST_WINDOW_MINUTES", 15),
            # Baseline is a quiet period that ends before the incident starts.
            baseline_window_minutes=_get_int("BASELINE_WINDOW_MINUTES", 120),
            baseline_offset_minutes=_get_int("BASELINE_OFFSET_MINUTES", 60),
            analysis_step_seconds=_get_int("ANALYSIS_STEP_SECONDS", 60),
            # Ratio of incident-window value to baseline value for each tier.
            anomaly_confirm_ratio=_get_float("ANOMALY_CONFIRM_RATIO", 3.0),
            anomaly_probable_ratio=_get_float("ANOMALY_PROBABLE_RATIO", 1.8),
            anomaly_possible_ratio=_get_float("ANOMALY_POSSIBLE_RATIO", 1.25),
            # Minimum weighted evidence score required before a confident RCA is written.
            min_evidence_score=_get_float("MIN_EVIDENCE_SCORE", 3.0),
        )

