import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import Settings
from .email_templates import build_email_html
from .models import AlertContext


class EmailNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_incident_report(self, subject: str, body: str, ctx: AlertContext) -> None:
        if not self.settings.smtp_host or not self.settings.smtp_to:
            print("SMTP_HOST or SMTP_TO missing. Incident report below:\n", body)
            return

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self.settings.smtp_from
        message["To"] = self.settings.smtp_to
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(build_email_html(ctx, body), "html", "utf-8"))

        recipients = [address.strip() for address in self.settings.smtp_to.split(",") if address.strip()]
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as server:
            if self.settings.smtp_use_tls:
                server.starttls()
            if self.settings.smtp_user and self.settings.smtp_password:
                server.login(self.settings.smtp_user, self.settings.smtp_password)
            server.sendmail(self.settings.smtp_from, recipients, message.as_string())
