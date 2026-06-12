"""Endpoint authentication dependencies.

Grafana's webhook can send an ``Authorization`` header or a custom header, so the
``/alert`` guard accepts a shared token via either ``Authorization: Bearer <token>``
or ``X-Webhook-Token``. Report endpoints are guarded by an API key (``X-API-Key``
or bearer). Comparisons are constant-time to avoid timing oracles.

If a secret is not configured the guard allows the request but logs a loud
warning — this preserves backwards compatibility while making the insecure state
visible. In production both secrets should always be set.
"""

import hmac
from typing import Callable, Optional

from fastapi import Header, HTTPException, status

from .config import Settings
from .logging_config import get_logger

logger = get_logger("security")


def _extract(authorization: Optional[str], custom: Optional[str]) -> Optional[str]:
    if custom:
        return custom.strip()
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return value
    return None


def _matches(expected: str, presented: Optional[str]) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(expected, presented)


def webhook_auth(settings: Settings) -> Callable:
    expected = settings.webhook_token

    async def dependency(
        authorization: Optional[str] = Header(default=None),
        x_webhook_token: Optional[str] = Header(default=None),
    ) -> None:
        if not expected:
            logger.warning(
                "WEBHOOK_TOKEN is not set — /alert is UNAUTHENTICATED. Set it in production."
            )
            return
        if not _matches(expected, _extract(authorization, x_webhook_token)):
            logger.warning("Rejected /alert request: invalid or missing webhook token.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing webhook token.",
            )

    return dependency


def reports_auth(settings: Settings) -> Callable:
    expected = settings.reports_api_key

    async def dependency(
        authorization: Optional[str] = Header(default=None),
        x_api_key: Optional[str] = Header(default=None),
    ) -> None:
        if not expected:
            logger.warning(
                "REPORTS_API_KEY is not set — /reports/* are UNAUTHENTICATED. Set it in production."
            )
            return
        if not _matches(expected, _extract(authorization, x_api_key)):
            logger.warning("Rejected /reports request: invalid or missing API key.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key.",
            )

    return dependency
