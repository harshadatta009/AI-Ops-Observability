from typing import Any, Dict

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from ai_incident_service.ai_reports import GroqReportGenerator
from ai_incident_service.config import Settings
from ai_incident_service.emailer import EmailNotifier
from ai_incident_service.grafana import GrafanaAlertParser
from ai_incident_service.observability import (
    LokiClient,
    ObservabilityCollector,
    PrometheusClient,
)
from ai_incident_service.pdf_report import PdfReportRenderer
from ai_incident_service.prompts import (
    CONSOLIDATED_RCA_PROMPT,
    DAILY_HEALTH_PROMPT,
    DB_ALERTS_PROMPT,
)


settings = Settings.from_env()
alert_parser = GrafanaAlertParser()
prometheus = PrometheusClient(settings.prometheus_url)
loki = LokiClient(
    settings.loki_url,
    max_log_lines=settings.max_log_lines,
    max_log_chars=settings.max_log_chars,
)
collector = ObservabilityCollector(
    prometheus=prometheus,
    loki=loki,
    lookback_minutes=settings.lookback_minutes,
)
report_generator = GroqReportGenerator(
    api_key=settings.groq_api_key,
    model=settings.groq_model,
    lookback_minutes=settings.lookback_minutes,
)
email_notifier = EmailNotifier(settings)
pdf_renderer = PdfReportRenderer(settings.brand_icon_path)

app = FastAPI(title="AI Incident Service", version="1.0.0")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/alert")
async def receive_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
    print("Incoming payload:", payload)
    alerts = alert_parser.parse(payload)

    if not alerts:
        return {"ok": True, "message": "No alerts found in payload"}

    reports = []
    for ctx in alerts:
        print(f"Processing alert: {ctx.alertname}")
        try:
            metrics = collector.alert_metrics(ctx)
            logs = collector.alert_logs(ctx)
            report = report_generator.incident_report(ctx, metrics, logs)
            subject = f"[{ctx.status.upper()}] AI Incident Report: {ctx.alertname}"
            email_notifier.send_incident_report(subject, report, ctx)
            reports.append(
                {"alertname": ctx.alertname, "status": ctx.status, "report": report}
            )
        except Exception as exc:
            print("Error:", str(exc))
            reports.append(
                {"alertname": ctx.alertname, "status": "error", "error": str(exc)}
            )

    return {"ok": True, "reports_generated": len(reports)}


@app.get("/reports/consolidated-rca/pdf")
async def consolidated_rca_pdf() -> StreamingResponse:
    return _pdf_response(
        title="Consolidated RCA Report",
        filename="consolidated_rca_report.pdf",
        report_type="Consolidated RCA",
        prompt_template=CONSOLIDATED_RCA_PROMPT,
        metrics=collector.system_metrics(),
        logs=collector.logs(db_only=False),
    )


@app.get("/reports/daily-health/pdf")
async def daily_health_pdf() -> StreamingResponse:
    return _pdf_response(
        title="Daily System Health Check Report",
        filename="daily_health_check_report.pdf",
        report_type="Daily Health Check",
        prompt_template=DAILY_HEALTH_PROMPT,
        metrics=collector.system_metrics(),
        logs=collector.logs(db_only=False),
    )


@app.get("/reports/db-alerts/pdf")
async def db_alerts_pdf() -> StreamingResponse:
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
