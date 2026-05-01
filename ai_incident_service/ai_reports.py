import json
from typing import Any, Dict

from groq import Groq

from .models import AlertContext
from .prompts import INCIDENT_PROMPT


class GroqReportGenerator:
    def __init__(self, api_key: str, model: str, lookback_minutes: int) -> None:
        self.api_key = api_key
        self.model = model
        self.lookback_minutes = lookback_minutes

    def incident_report(
        self,
        ctx: AlertContext,
        metrics: Dict[str, Any],
        logs: Dict[str, Any],
    ) -> str:
        prompt = f"""
{INCIDENT_PROMPT}

Alert context:
{ctx.model_dump_json(indent=2)}

Prometheus metrics from the last {self.lookback_minutes} minutes:
{json.dumps(metrics, indent=2)[:3000]}

Relevant Loki logs from the last {self.lookback_minutes} minutes:
{json.dumps(logs, indent=2)[:3000]}
"""
        return self._complete(prompt, "You generate clear, accurate SRE incident reports.")

    def operational_report(
        self,
        report_type: str,
        prompt_template: str,
        metrics: Dict[str, Any],
        logs: Dict[str, Any],
    ) -> str:
        prompt = f"""
{prompt_template}

Report type:
{report_type}

Prometheus metrics:
{json.dumps(metrics, indent=2)[:3000]}

Loki logs:
{json.dumps(logs, indent=2)[:3000]}

Generate the final report using the requested structure only.
Do not include raw JSON, raw log lines, tool output, or explanatory preamble.
Use Markdown tables when requested by the report template.
Use short bullets and severity labels such as [LOW], [MEDIUM], [HIGH], [CRITICAL].
"""
        return self._complete(
            prompt,
            "You generate accurate, concise SRE reports for operations teams.",
        )

    def _complete(self, prompt: str, system_message: str) -> str:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")

        client = Groq(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1800,
        )
        return response.choices[0].message.content
