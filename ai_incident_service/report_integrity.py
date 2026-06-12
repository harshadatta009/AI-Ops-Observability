"""Make operational reports auditable and fabrication-resistant.

The original pipeline dumped raw Prometheus JSON, truncated to 3000 chars, into
the prompt. With many services the JSON was cut mid-stream, so the model invented
the tail (the tell-tale ``15.123456789012345`` values and MB-rendered-as-%).

Two defences live here:

  1. ``summarize_metrics`` — turn raw instant-query results into a compact, fully
     labelled, 2-decimal-rounded structure. This removes the need to truncate and
     gives the model clean ``service: value`` pairs it can quote verbatim.

  2. ``find_fabricated_numbers`` — a post-generation audit. Because every source
     value is rounded to 2 decimals, ANY number in the report carrying 3+ decimal
     places cannot have come from the data and is therefore fabricated. We surface
     those rather than silently trusting the model.
"""

import re
from typing import Any, Dict, List

ROUND_DP = 2
_HIGH_PRECISION = re.compile(r"\d+\.\d{3,}")


def _series_label(metric: Dict[str, Any]) -> str:
    for key in ("service_name", "name", "instance", "mountpoint", "job", "container"):
        if metric.get(key):
            return str(metric[key])
    return "value" if not metric else ", ".join(f"{k}={v}" for k, v in metric.items())


def summarize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Convert raw Prometheus results into clean, rounded, labelled data."""
    summary: Dict[str, Any] = {}
    unavailable: List[str] = []

    for name, result in metrics.items():
        if isinstance(result, dict) and "error" in result:
            unavailable.append(name)
            continue
        if not isinstance(result, list) or not result:
            unavailable.append(name)
            continue

        series: List[Dict[str, Any]] = []
        for item in result:
            value = item.get("value")
            if not value or len(value) < 2:
                continue
            try:
                numeric = round(float(value[1]), ROUND_DP)
            except (TypeError, ValueError):
                continue
            series.append({"service": _series_label(item.get("metric", {})), "value": numeric})

        if series:
            series.sort(key=lambda row: row["value"], reverse=True)
            summary[name] = series
        else:
            unavailable.append(name)

    return {"metrics": summary, "unavailable_queries": sorted(set(unavailable))}


def allowed_number_tokens(summary: Dict[str, Any]) -> set:
    """Every value the report is permitted to cite, as rendered strings."""
    tokens = set()
    for series in summary.get("metrics", {}).values():
        for row in series:
            value = row["value"]
            tokens.add(f"{value}")
            tokens.add(f"{value:.2f}")
            tokens.add(f"{int(value)}")
    return tokens


def find_fabricated_numbers(report: str, summary: Dict[str, Any]) -> List[str]:
    """Return high-precision numbers in the report absent from the source data."""
    allowed = allowed_number_tokens(summary)
    violations = []
    for match in _HIGH_PRECISION.findall(report):
        if match in allowed:
            continue
        # also accept the 2dp rounding of the matched number, just in case
        try:
            if f"{round(float(match), ROUND_DP):.2f}" in allowed:
                continue
        except ValueError:
            pass
        violations.append(match)
    return sorted(set(violations))


def integrity_notice(violations: List[str]) -> str:
    """A visible, auditable footer appended when fabricated numbers are detected."""
    sample = ", ".join(violations[:8])
    return (
        "\n\n## Data Integrity Notice\n"
        f"- [HIGH] Automated audit flagged {len(violations)} value(s) not present in the "
        "source metrics (possible model fabrication). Treat the figures below as "
        f"UNVERIFIED and confirm against Prometheus before acting: {sample}."
    )
