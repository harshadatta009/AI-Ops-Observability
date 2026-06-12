import json
from typing import Any, Dict

from groq import Groq

from .logging_config import get_logger
from .models import AlertContext, EvidenceBundle
from .prompts import INCIDENT_PROMPT
from .report_integrity import (
    find_fabricated_numbers,
    integrity_notice,
    summarize_metrics,
)

logger = get_logger("ai_reports")


class GroqReportGenerator:
    def __init__(
        self,
        api_key: str,
        model: str,
        lookback_minutes: int,
        timeout_seconds: int = 45,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.lookback_minutes = lookback_minutes
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def incident_report(
        self,
        ctx: AlertContext,
        evidence: EvidenceBundle,
        logs: Dict[str, Any],
    ) -> str:
        # The evidence bundle is the primary input. It is intentionally NOT heavily
        # truncated: the deep, multi-window, multi-signal analysis is the whole point.
        gating = (
            "SUFFICIENT — a confident root cause may be stated if the evidence supports it."
            if evidence.sufficient_evidence
            else "INSUFFICIENT — do NOT assert a confident root cause; emphasize next steps."
        )
        prompt = f"""
{INCIDENT_PROMPT}

Alert context:
{ctx.model_dump_json(indent=2)}

EVIDENCE GATING DECISION: {gating}
Overall evidence score: {evidence.evidence_score} (threshold for sufficiency applied by analyzer).

Deep analysis evidence bundle (baseline vs pre/during/post windows, per-signal
statistics, classifications, causality, correlations, and the exact queries used):
{evidence.model_dump_json(indent=2)[:16000]}

Relevant Loki logs (corroborating context only — do NOT derive the root cause from
these alone):
{json.dumps(logs, indent=2)[:4000]}
"""
        return self._complete(
            prompt,
            "You generate conservative, evidence-backed SRE incident reports and "
            "refuse to overstate conclusions beyond the supplied evidence.",
            max_tokens=3500,
        )

    def operational_report(
        self,
        report_type: str,
        prompt_template: str,
        metrics: Dict[str, Any],
        logs: Dict[str, Any],
    ) -> str:
        # Summarize server-side so the model receives clean, fully-labelled,
        # 2-decimal values it can quote verbatim — never truncated raw JSON.
        summary = summarize_metrics(metrics)
        unavailable = summary["unavailable_queries"]

        prompt = f"""
{prompt_template}

Report type:
{report_type}

ANTI-FABRICATION RULES (mandatory):
- Use ONLY the numbers present in the summarized metrics below. Copy them verbatim.
- NEVER invent, interpolate, or "complete" a number. If a value is not present,
  write "Insufficient data" instead of guessing.
- Do not add decimal precision beyond what is given (values are rounded to 2 dp).
- For any query listed as unavailable, state the data was unavailable; do not fabricate it.

Summarized Prometheus metrics (service -> value, already rounded and sorted):
{json.dumps(summary["metrics"], indent=2)[:14000]}

Queries with no data available (do NOT invent values for these):
{json.dumps(unavailable, indent=2)}

Loki logs:
{json.dumps(logs, indent=2)[:4000]}

Generate the final report using the requested structure only.
Do not include raw JSON, raw log lines, tool output, or explanatory preamble.
Use Markdown tables when requested by the report template.
Use short bullets and severity labels such as [LOW], [MEDIUM], [HIGH], [CRITICAL].
"""
        report = self._complete(
            prompt,
            "You generate accurate, concise SRE reports for operations teams and never "
            "invent metric values — every number must come from the supplied data.",
        )

        # Post-generation audit: any 3+ decimal number cannot have come from the
        # 2dp source data, so it is fabrication. Flag it instead of trusting it.
        violations = find_fabricated_numbers(report, summary)
        if violations:
            logger.warning(
                "Fabricated numbers detected in %s report: %s", report_type, violations
            )
            report += integrity_notice(violations)
        return report

    def _complete(self, prompt: str, system_message: str, max_tokens: int = 1800) -> str:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")

        # The Groq SDK retries transient errors (429/5xx/network) with backoff
        # internally; we set an explicit per-request timeout so a hung LLM call
        # cannot stall the worker indefinitely.
        client = Groq(
            api_key=self.api_key,
            timeout=self.timeout_seconds,
            max_retries=self.max_retries,
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
