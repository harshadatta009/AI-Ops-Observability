import uuid
from typing import Any, Dict

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse

from ai_incident_service.ai_reports import GroqReportGenerator
from ai_incident_service.analysis import IncidentAnalyzer
from ai_incident_service.config import Settings
from ai_incident_service.emailer import EmailNotifier
from ai_incident_service.grafana import GrafanaAlertParser
from ai_incident_service.logging_config import (
    configure_logging,
    get_logger,
    register_secrets,
    request_id_var,
)
from ai_incident_service.models import GrafanaWebhook
from ai_incident_service.observability import (
    LokiClient,
    ObservabilityCollector,
    PrometheusClient,
    build_retrying_session,
)
from ai_incident_service.pdf_report import PdfReportRenderer
from ai_incident_service.prompts import (
    CONSOLIDATED_RCA_PROMPT,
    DAILY_HEALTH_PROMPT,
    DB_ALERTS_PROMPT,
)
from ai_incident_service.security import reports_auth, webhook_auth


settings = Settings.from_env()
configure_logging(level=settings.log_level, fmt=settings.log_format)
register_secrets(
    [settings.groq_api_key, settings.smtp_password, settings.webhook_token, settings.reports_api_key]
)
logger = get_logger("app")

alert_parser = GrafanaAlertParser()
# One retrying HTTP session shared by both observability clients.
http_session = build_retrying_session(settings.http_max_retries)
prometheus = PrometheusClient(
    settings.prometheus_url,
    timeout_seconds=settings.http_timeout_seconds,
    session=http_session,
)
loki = LokiClient(
    settings.loki_url,
    max_log_lines=settings.max_log_lines,
    max_log_chars=settings.max_log_chars,
    timeout_seconds=settings.http_timeout_seconds,
    session=http_session,
)
collector = ObservabilityCollector(
    prometheus=prometheus,
    loki=loki,
    lookback_minutes=settings.lookback_minutes,
)
incident_analyzer = IncidentAnalyzer(prometheus=prometheus, settings=settings)
report_generator = GroqReportGenerator(
    api_key=settings.groq_api_key,
    model=settings.groq_model,
    lookback_minutes=settings.lookback_minutes,
    timeout_seconds=settings.groq_timeout_seconds,
    max_retries=settings.groq_max_retries,
)
email_notifier = EmailNotifier(settings)
pdf_renderer = PdfReportRenderer(settings.brand_icon_path)

require_webhook = webhook_auth(settings)
require_reports_key = reports_auth(settings)

app = FastAPI(title="AI Incident Service", version="1.1.0")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/alert")
async def receive_alert(
    payload: GrafanaWebhook, _: None = Depends(require_webhook)
) -> Dict[str, Any]:
    request_id_var.set(uuid.uuid4().hex[:12])
    alerts = alert_parser.parse(payload.model_dump())
    logger.info("Received webhook with %d alert(s)", len(alerts))

    if not alerts:
        return {"ok": True, "message": "No alerts found in payload"}

    reports = []
    for ctx in alerts:
        logger.info("Processing alert: %s", ctx.alertname)
        try:
            # Deep, evidence-based analysis across baseline/pre/during/post windows
            # and many correlated signals — not just the latest logs/metric spike.
            evidence = incident_analyzer.analyze(ctx)
            logs = collector.alert_logs(ctx)
            report = report_generator.incident_report(ctx, evidence, logs)

            verdict = "RCA" if evidence.sufficient_evidence else "INSUFFICIENT-EVIDENCE"
            subject = (
                f"[{ctx.status.upper()}][{verdict}] AI Incident Report: {ctx.alertname}"
            )
            email_notifier.send_incident_report(subject, report, ctx)
            logger.info(
                "Generated %s report for %s (score=%s)",
                verdict,
                ctx.alertname,
                evidence.evidence_score,
            )
            reports.append(
                {
                    "alertname": ctx.alertname,
                    "status": ctx.status,
                    "sufficient_evidence": evidence.sufficient_evidence,
                    "evidence_score": evidence.evidence_score,
                    "report": report,
                }
            )
        except Exception as exc:
            logger.exception("Failed to process alert %s: %s", ctx.alertname, exc)
            reports.append(
                {"alertname": ctx.alertname, "status": "error", "error": str(exc)}
            )

    return {"ok": True, "reports_generated": len(reports)}


@app.get("/reports/consolidated-rca/pdf")
async def consolidated_rca_pdf(_: None = Depends(require_reports_key)) -> StreamingResponse:
    return _pdf_response(
        title="Consolidated RCA Report",
        filename="consolidated_rca_report.pdf",
        report_type="Consolidated RCA",
        prompt_template=CONSOLIDATED_RCA_PROMPT,
        metrics=collector.system_metrics(),
        logs=collector.logs(db_only=False),
    )


@app.get("/reports/daily-health/pdf")
async def daily_health_pdf(_: None = Depends(require_reports_key)) -> StreamingResponse:
    return _pdf_response(
        title="Daily System Health Check Report",
        filename="daily_health_check_report.pdf",
        report_type="Daily Health Check",
        prompt_template=DAILY_HEALTH_PROMPT,
        metrics=collector.system_metrics(),
        logs=collector.logs(db_only=False),
    )


@app.get("/reports/db-alerts/pdf")
async def db_alerts_pdf(_: None = Depends(require_reports_key)) -> StreamingResponse:
    return _pdf_response(
        title="Database Alerts Report",
        filename="db_alerts_report.pdf",
        report_type="Database Alerts",
        prompt_template=DB_ALERTS_PROMPT,
        metrics=collector.db_metrics(),
        logs=collector.logs(db_only=True),
    )


def _pdf_response(
    title: str,
    filename: str,
    report_type: str,
    prompt_template: str,
    metrics: Dict[str, Any],
    logs: Dict[str, Any],
) -> StreamingResponse:
    report = report_generator.operational_report(
        report_type=report_type,
        prompt_template=prompt_template,
        metrics=metrics,
        logs=logs,
    )
    pdf = pdf_renderer.render(title, report)
    return StreamingResponse(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
