import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import Settings
from .email_templates import build_email_html
from .logging_config import get_logger
from .models import AlertContext

logger = get_logger("emailer")


class EmailNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_incident_report(self, subject: str, body: str, ctx: AlertContext) -> None:
        if not self.settings.smtp_host or not self.settings.smtp_to:
            logger.warning(
                "SMTP_HOST or SMTP_TO missing; skipping email delivery for '%s'.", subject
            )
            return

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self.settings.smtp_from
        message["To"] = self.settings.smtp_to
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(build_email_html(ctx, body), "html", "utf-8"))

        recipients = [address.strip() for address in self.settings.smtp_to.split(",") if address.strip()]
        self._send_with_retry(recipients, message)

    def _send_with_retry(self, recipients, message) -> None:
        attempts = max(1, self.settings.http_max_retries)
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                with smtplib.SMTP(
                    self.settings.smtp_host, self.settings.smtp_port, timeout=20
                ) as server:
                    if self.settings.smtp_use_tls:
                        server.starttls()
                    if self.settings.smtp_user and self.settings.smtp_password:
                        server.login(self.settings.smtp_user, self.settings.smtp_password)
                    server.sendmail(
                        self.settings.smtp_from, recipients, message.as_string()
                    )
                return
            except Exception as exc:  # noqa: BLE001 — retry any SMTP failure
                last_exc = exc
                logger.warning("SMTP send attempt %d/%d failed: %s", attempt, attempts, exc)
                if attempt < attempts:
                    time.sleep(0.5 * attempt)
        logger.error("Email delivery failed after %d attempts: %s", attempts, last_exc)
        raise last_exc
