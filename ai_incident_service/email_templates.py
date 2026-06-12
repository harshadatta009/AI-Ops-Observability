import html
import re
from typing import Dict, List, Optional

from .models import AlertContext


SECTION_TITLES = [
    "Incident Summary",
    "Affected Services",
    "Timeline of Events",
    "Metrics Analyzed",
    "Evidence Supporting the Conclusion",
    "Confirmed Issues",
    "Probable Causes",
    "Possible Contributing Factors",
    "False Positives / Weak Signals",
    "Root Cause Hypothesis",
    "Confidence Score",
    "False-Positive Checks Performed",
    "Prometheus Queries & Time Ranges Used",
    "Recommended Remediation Steps",
    "Additional Data Needed",
]


def normalize_heading(value: str) -> str:
    value = re.sub(r"^\s*\d+[\).\s-]*", "", value)
    value = value.strip().strip("*#:- ")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_report_sections(report: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current_title = "Incident Summary"
    sections[current_title] = []
    known = {normalize_heading(title): title for title in SECTION_TITLES}

    for line in report.splitlines():
        stripped = line.strip()
        heading_candidate = normalize_heading(stripped)
        if heading_candidate in known and len(stripped) <= 80:
            current_title = known[heading_candidate]
            sections.setdefault(current_title, [])
            continue

        severity_match = re.match(r"^\s*\**Severity\**\s*:\s*(.*)$", stripped, re.I)
        if severity_match:
            sections["Severity"] = [severity_match.group(1).strip()]
            current_title = "Severity"
            continue

        sections.setdefault(current_title, []).append(line)

    return {
        title: "\n".join(lines).strip()
        for title, lines in sections.items()
        if "\n".join(lines).strip()
    }


def build_email_html(ctx: AlertContext, report: str) -> str:
    sections = parse_report_sections(report)
    severity = _detect_severity(ctx, sections)
    status = (ctx.status or "unknown").upper()
    status_style = _badge_style(ctx.status)
    severity_style = _badge_style(severity)
    service = ctx.service_name or ctx.container_name or ctx.instance or "Unknown service"
    summary = sections.get("Incident Summary") or ctx.summary or "Incident details are being analyzed."

    cards = []
    for title in SECTION_TITLES:
        if title == "Incident Summary":
            continue
        content = sections.get(title)
        if not content:
            continue
        cards.append(
            '<tr><td style="padding:0 0 14px 0;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="border:1px solid #e2e8f0;border-radius:10px;background:#ffffff;">'
            '<tr><td style="padding:18px 20px;">'
            f'<h2 style="margin:0 0 10px 0;color:#0f172a;font-size:16px;line-height:1.3;">{html.escape(title)}</h2>'
            f"{_render_report_body(content)}"
            "</td></tr></table></td></tr>"
        )

    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f1f5f9;padding:24px 0;">
      <tr>
        <td align="center" style="padding:0 12px;">
          <table role="presentation" width="720" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e2e8f0;">
            <tr>
              <td style="background:#0f172a;padding:24px 28px;">
                <div style="color:#93c5fd;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px;">AI Incident Report</div>
                <h1 style="margin:0;color:#ffffff;font-size:24px;line-height:1.25;">{html.escape(ctx.alertname)}</h1>
                <p style="margin:10px 0 0 0;color:#cbd5e1;font-size:14px;line-height:1.5;">{html.escape(service)}</p>
                <div style="margin-top:16px;">
                  <span style="display:inline-block;background:{status_style['bg']};color:{status_style['fg']};border:1px solid {status_style['border']};border-radius:999px;padding:6px 10px;font-size:12px;font-weight:700;margin-right:8px;">{html.escape(status)}</span>
                  <span style="display:inline-block;background:{severity_style['bg']};color:{severity_style['fg']};border:1px solid {severity_style['border']};border-radius:999px;padding:6px 10px;font-size:12px;font-weight:700;">Severity: {html.escape(severity)}</span>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:24px 28px 12px 28px;background:#ffffff;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-bottom:1px solid #e2e8f0;padding-bottom:16px;">
                  {_render_meta_row("Started", _format_timestamp(ctx.starts_at))}
                  {_render_meta_row("Service", ctx.service_name)}
                  {_render_meta_row("Container", ctx.container_name)}
                  {_render_meta_row("Instance", ctx.instance)}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 28px 8px 28px;">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">
                  <tr><td style="padding:18px 20px;">
                    <h2 style="margin:0 0 10px 0;color:#0f172a;font-size:16px;line-height:1.3;">Incident Summary</h2>
                    {_render_report_body(summary)}
                  </td></tr>
                </table>
              </td>
            </tr>
            <tr><td style="padding:10px 28px 18px 28px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{''.join(cards)}</table></td></tr>
            <tr>
              <td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 28px;color:#64748b;font-size:12px;line-height:1.5;">
                Generated automatically by the AI Incident Service from Grafana alert context, Prometheus metrics, and Loki logs.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _detect_severity(ctx: AlertContext, sections: Dict[str, str]) -> str:
    for candidate in [ctx.severity, sections.get("Severity", ""), " ".join(sections.values())]:
        match = re.search(r"\b(critical|high|medium|low|warning|unknown)\b", candidate or "", re.I)
        if match:
            return match.group(1).capitalize()
    return "Unknown"


def _badge_style(value: str) -> Dict[str, str]:
    normalized = (value or "").lower()
    if normalized in {"critical", "firing"}:
        return {"bg": "#fee2e2", "fg": "#991b1b", "border": "#fecaca"}
    if normalized == "high":
        return {"bg": "#ffedd5", "fg": "#9a3412", "border": "#fed7aa"}
    if normalized in {"medium", "warning"}:
        return {"bg": "#fef3c7", "fg": "#92400e", "border": "#fde68a"}
    if normalized in {"low", "resolved", "ok"}:
        return {"bg": "#dcfce7", "fg": "#166534", "border": "#bbf7d0"}
    return {"bg": "#e0f2fe", "fg": "#075985", "border": "#bae6fd"}


def _clean_markdown_text(value: str) -> str:
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    return value.strip()


def _render_report_body(value: str) -> str:
    lines = [line.rstrip() for line in value.splitlines()]
    html_parts: List[str] = []
    list_items: List[str] = []

    def flush_list() -> None:
        if not list_items:
            return
        items = "".join(f'<li style="margin:0 0 8px 0;">{item}</li>' for item in list_items)
        html_parts.append(
            '<ul style="margin:8px 0 0 18px;padding:0;color:#334155;'
            f'font-size:14px;line-height:1.55;">{items}</ul>'
        )
        list_items.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue
        bullet = re.match(r"^(?:[-*]|\d+[\).])\s+(.*)$", stripped)
        if bullet:
            list_items.append(html.escape(_clean_markdown_text(bullet.group(1))))
            continue
        flush_list()
        html_parts.append(
            '<p style="margin:0 0 10px 0;color:#334155;font-size:14px;'
            f'line-height:1.6;">{html.escape(_clean_markdown_text(stripped))}</p>'
        )
    flush_list()
    return "".join(html_parts) or (
        '<p style="margin:0;color:#64748b;font-size:14px;line-height:1.6;">No evidence was provided for this section.</p>'
    )


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "Not provided"
    return value.replace("T", " ").replace("Z", " UTC")


def _render_meta_row(label: str, value: Optional[str]) -> str:
    clean_value = html.escape(value or "Not provided")
    return (
        '<tr>'
        '<td style="padding:8px 0;color:#64748b;font-size:12px;width:120px;text-transform:uppercase;letter-spacing:.04em;">'
        f"{html.escape(label)}</td>"
        '<td style="padding:8px 0;color:#0f172a;font-size:14px;font-weight:600;">'
        f"{clean_value}</td>"
        "</tr>"
    )

