"""Deep, evidence-based incident analysis.

This module is the heart of the "don't trust surface-level logs" requirement. It
does NOT ask the LLM to look at a few recent log lines. Instead it:

  1. Establishes four time windows around the suspected incident — a quiet
     ``baseline`` well before it, plus ``pre`` / ``during`` / ``post`` windows so
     behaviour is compared before, during, and after the event.
  2. Queries a broad catalogue of correlated signals (error rate, latency,
     request volume, saturation, CPU/memory/disk/network, container restarts,
     deployment proxies, dependency health) as Prometheus *range* vectors.
  3. Summarises each signal per window (min/max/avg/p95) and compares the
     incident window against the historical baseline.
  4. Classifies every signal as confirmed / probable / possible / false-positive
     / no-data, with a confidence score, and infers whether each is a primary
     cause or a downstream effect.
  5. Scores the total evidence and decides whether there is *enough* to justify a
     confident root-cause report. If not, it records the gaps and the next
     debugging steps instead of fabricating a conclusion.

The output is an :class:`EvidenceBundle` consumed by the report generator. The
heuristics are intentionally transparent and conservative.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .models import (
    AlertContext,
    AnalysisWindow,
    EvidenceBundle,
    Finding,
    SignalEvidence,
    SignalStats,
)
from .observability import PrometheusClient

SERVICE_LABEL = "container_label_com_docker_swarm_service_name"

# Causality ranking: lower rank = more likely to be a root cause rather than a
# symptom. Resource exhaustion is the most fundamental cause (restarts are often
# just its OOM-kill symptom); a restart with no saturation points to a deploy.
# Error rate and latency are the downstream effects you notice first.
_CAUSE_RANK = {
    "saturation": 1,
    "memory": 1,
    "disk": 1,
    "restart": 2,
    "deployment": 2,
    "cpu": 2,
    "network": 3,
    "request_volume": 3,
    "dependency": 4,
    "error_rate": 5,
    "latency": 6,
}

_CLASS_WEIGHT = {
    "confirmed": 3.0,
    "probable": 2.0,
    "possible": 1.0,
    "false_positive": 0.0,
    "no_data": 0.0,
}


class SignalSpec:
    """One signal to evaluate, with the PromQL that produces it.

    ``strategy`` controls how the incident window is judged:
      - ``ratio``    : incident value vs baseline value (multiplicative).
      - ``absolute`` : incident value vs fixed warn/critical thresholds (for
                       percentages such as saturation).
      - ``count``    : "did this happen at all" vs baseline (for restarts).
    """

    def __init__(
        self,
        name: str,
        category: str,
        description: str,
        query: str,
        unit: str = "",
        aggregate: str = "avg",
        strategy: str = "ratio",
        warn: Optional[float] = None,
        critical: Optional[float] = None,
        optional: bool = False,
    ) -> None:
        self.name = name
        self.category = category
        self.description = description
        self.query = query
        self.unit = unit
        self.aggregate = aggregate  # which per-window stat drives comparison
        self.strategy = strategy
        self.warn = warn
        self.critical = critical
        self.optional = optional


class IncidentAnalyzer:
    def __init__(self, prometheus: PrometheusClient, settings: Settings) -> None:
        self.prometheus = prometheus
        self.settings = settings

    # -- public API -----------------------------------------------------------

    def analyze(self, ctx: AlertContext) -> EvidenceBundle:
        incident_time = self._incident_time(ctx)
        windows = self._build_windows(incident_time)
        specs = self._signal_catalog(ctx)

        signals: List[SignalEvidence] = []
        query_errors: List[str] = []

        for spec in specs:
            evidence, errors = self._evaluate_signal(spec, windows)
            signals.append(evidence)
            query_errors.extend(errors)

        findings = [self._classify(spec, ev) for spec, ev in zip(specs, signals)]
        self._assign_causality(findings)

        correlations = self._correlate(findings, signals)
        missing = self._missing_signals(specs, findings)
        score, sufficient = self._score(findings)
        hypothesis = self._root_cause_hypothesis(findings) if sufficient else None
        next_steps = self._next_steps(findings, missing, sufficient)

        return EvidenceBundle(
            incident_time=incident_time.isoformat(),
            target=self._target_label(ctx),
            windows=[
                AnalysisWindow(
                    name=w["name"],
                    start=w["start"].isoformat(),
                    end=w["end"].isoformat(),
                    description=w["description"],
                )
                for w in windows
            ],
            signals=signals,
            findings=findings,
            correlations=correlations,
            root_cause_hypothesis=hypothesis,
            evidence_score=round(score, 2),
            sufficient_evidence=sufficient,
            missing_signals=missing,
            next_steps=next_steps,
            query_errors=sorted(set(query_errors)),
        )

    # -- windows --------------------------------------------------------------

    def _incident_time(self, ctx: AlertContext) -> datetime:
        if ctx.starts_at:
            raw = ctx.starts_at.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                # Guard against pathological/zero timestamps from some webhooks.
                if parsed.year > 2000:
                    return parsed
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    def _build_windows(self, t: datetime) -> List[Dict[str, Any]]:
        s = self.settings
        half = timedelta(minutes=s.incident_window_minutes) / 2
        during_start = t - half
        during_end = t + half
        pre_start = during_start - timedelta(minutes=s.pre_window_minutes)
        post_end = during_end + timedelta(minutes=s.post_window_minutes)
        baseline_end = t - timedelta(minutes=s.baseline_offset_minutes)
        baseline_start = baseline_end - timedelta(minutes=s.baseline_window_minutes)
        return [
            {
                "name": "baseline",
                "start": baseline_start,
                "end": baseline_end,
                "description": "Quiet reference period before the incident.",
            },
            {
                "name": "pre",
                "start": pre_start,
                "end": during_start,
                "description": "Run-up immediately before the alert.",
            },
            {
                "name": "during",
                "start": during_start,
                "end": during_end,
                "description": "Window straddling the alert start time.",
            },
            {
                "name": "post",
                "start": during_end,
                "end": post_end,
                "description": "Recovery window after the incident window.",
            },
        ]

    # -- signal catalogue -----------------------------------------------------

    def _target_label(self, ctx: AlertContext) -> str:
        return ctx.service_name or ctx.container_name or ctx.instance or "whole system"

    def _selectors(self, ctx: AlertContext) -> Tuple[str, str]:
        """Return (cadvisor_match, app_match) label-selector bodies (no braces)."""
        if ctx.service_name:
            cadvisor = f'{SERVICE_LABEL}=~"{ctx.service_name}"'
            app = f'service_name=~"{ctx.service_name}"'
        elif ctx.container_name:
            cadvisor = f'name=~".*{ctx.container_name}.*"'
            app = f'service_name=~".*{ctx.container_name}.*"'
        else:
            cadvisor = ""
            app = ""
        return cadvisor, app

    def _signal_catalog(self, ctx: AlertContext) -> List[SignalSpec]:
        sel, app = self._selectors(ctx)
        cad = "{" + sel + "}"
        cad_lim = "{" + sel + "}" if sel else "{}"
        appb = "{" + app + "}" if app else "{}"

        return [
            # --- application-level signals (optional; may be absent) ----------
            SignalSpec(
                name="error_rate_5xx",
                category="error_rate",
                description="Rate of HTTP 5xx responses.",
                query=f'sum(rate(http_requests_total{{{app + "," if app else ""}status=~"5.."}}[5m]))',
                unit="errors/s",
                strategy="ratio",
                optional=True,
            ),
            SignalSpec(
                name="request_volume",
                category="request_volume",
                description="Total request throughput.",
                query=f"sum(rate(http_requests_total{appb}[5m]))",
                unit="req/s",
                strategy="ratio",
                optional=True,
            ),
            SignalSpec(
                name="latency_p95",
                category="latency",
                description="95th percentile request latency.",
                query=(
                    "histogram_quantile(0.95, sum by (le) "
                    f"(rate(http_request_duration_seconds_bucket{appb}[5m])))"
                ),
                unit="s",
                strategy="ratio",
                optional=True,
            ),
            SignalSpec(
                name="dependency_errors",
                category="dependency",
                description="Failed outbound/dependency calls.",
                query=(
                    "sum(rate(http_client_requests_seconds_count"
                    f'{{{app + "," if app else ""}status=~"5..|0"}}[5m]))'
                ),
                unit="errors/s",
                strategy="ratio",
                optional=True,
            ),
            # --- container resource signals (cadvisor) ------------------------
            SignalSpec(
                name="container_cpu",
                category="cpu",
                description="Container CPU usage (cores).",
                query=f"sum(rate(container_cpu_usage_seconds_total{cad}[5m])) * 100",
                unit="% of a core",
                strategy="ratio",
            ),
            SignalSpec(
                name="container_memory",
                category="memory",
                description="Container resident memory.",
                query=f"sum(container_memory_usage_bytes{cad}) / 1024 / 1024",
                unit="MB",
                strategy="ratio",
            ),
            SignalSpec(
                name="memory_saturation",
                category="saturation",
                description="Container memory used vs its limit.",
                query=(
                    f"max(container_memory_usage_bytes{cad_lim} "
                    f"/ (container_spec_memory_limit_bytes{cad_lim} > 0)) * 100"
                ),
                unit="% of limit",
                aggregate="max",
                strategy="absolute",
                warn=80.0,
                critical=95.0,
                optional=True,
            ),
            SignalSpec(
                name="network_receive",
                category="network",
                description="Inbound container network throughput.",
                query=f"sum(rate(container_network_receive_bytes_total{cad}[5m])) / 1024",
                unit="KB/s",
                strategy="ratio",
                optional=True,
            ),
            SignalSpec(
                name="network_transmit",
                category="network",
                description="Outbound container network throughput.",
                query=f"sum(rate(container_network_transmit_bytes_total{cad}[5m])) / 1024",
                unit="KB/s",
                strategy="ratio",
                optional=True,
            ),
            SignalSpec(
                name="container_restarts",
                category="restart",
                description="Container/pod restarts (also a deployment proxy).",
                query=f"sum(changes(container_start_time_seconds{cad}[5m]))",
                unit="restarts/5m",
                aggregate="max",
                strategy="count",
            ),
            # --- host / infrastructure signals --------------------------------
            SignalSpec(
                name="host_cpu",
                category="cpu",
                description="Host CPU utilisation.",
                query='100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
                unit="%",
                aggregate="max",
                strategy="absolute",
                warn=80.0,
                critical=92.0,
            ),
            SignalSpec(
                name="host_memory",
                category="saturation",
                description="Host memory utilisation.",
                query=(
                    "100 * (1 - (avg(node_memory_MemAvailable_bytes) "
                    "/ avg(node_memory_MemTotal_bytes)))"
                ),
                unit="%",
                aggregate="max",
                strategy="absolute",
                warn=85.0,
                critical=95.0,
            ),
            SignalSpec(
                name="host_disk",
                category="disk",
                description="Host filesystem utilisation.",
                query=(
                    "max(100 * (1 - (node_filesystem_avail_bytes"
                    '{fstype!~"tmpfs|overlay",mountpoint!~"/run.*|/var/lib/docker/.*"} '
                    "/ node_filesystem_size_bytes"
                    '{fstype!~"tmpfs|overlay",mountpoint!~"/run.*|/var/lib/docker/.*"})))'
                ),
                unit="%",
                aggregate="max",
                strategy="absolute",
                warn=80.0,
                critical=90.0,
            ),
        ]

    # -- evaluation -----------------------------------------------------------

    def _evaluate_signal(
        self, spec: SignalSpec, windows: List[Dict[str, Any]]
    ) -> Tuple[SignalEvidence, List[str]]:
        errors: List[str] = []
        window_stats: Dict[str, SignalStats] = {}

        for w in windows:
            result = self.prometheus.query_range(
                spec.query, w["start"], w["end"], self.settings.analysis_step_seconds
            )
            if isinstance(result, dict) and "error" in result:
                errors.append(f'{spec.name}: {result["error"]}')
                window_stats[w["name"]] = SignalStats(has_data=False)
                continue
            window_stats[w["name"]] = self._summarize(result)

        baseline = window_stats.get("baseline")
        during = window_stats.get("during")
        baseline_value = self._window_value(baseline, spec.aggregate)
        incident_value = self._window_value(during, spec.aggregate)

        ratio: Optional[float] = None
        delta: Optional[float] = None
        if baseline_value is not None and incident_value is not None:
            delta = incident_value - baseline_value
            if baseline_value > 1e-9:
                ratio = incident_value / baseline_value
            elif incident_value > 1e-9:
                ratio = float("inf")  # went from ~zero to something

        evidence = SignalEvidence(
            name=spec.name,
            category=spec.category,
            description=spec.description,
            query=spec.query,
            unit=spec.unit,
            optional=spec.optional,
            windows=window_stats,
            baseline_value=baseline_value,
            incident_value=incident_value,
            ratio=None if ratio == float("inf") else ratio,
            delta=delta,
        )
        return evidence, errors

    def _summarize(self, result: Any) -> SignalStats:
        if not isinstance(result, list) or not result:
            return SignalStats(has_data=False)

        values: List[float] = []
        for series in result:
            for _, raw in series.get("values", []):
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    continue
                if math.isnan(val) or math.isinf(val):
                    continue  # drop NaN/Inf (e.g. empty histogram quantiles)
                values.append(val)

        if not values:
            return SignalStats(has_data=False, series_count=len(result))

        ordered = sorted(values)
        p95_idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return SignalStats(
            has_data=True,
            min=round(ordered[0], 4),
            max=round(ordered[-1], 4),
            avg=round(sum(values) / len(values), 4),
            p95=round(ordered[p95_idx], 4),
            last=round(values[-1], 4),
            sample_count=len(values),
            series_count=len(result),
        )

    @staticmethod
    def _window_value(stats: Optional[SignalStats], aggregate: str) -> Optional[float]:
        if stats is None or not stats.has_data:
            return None
        return getattr(stats, aggregate, stats.avg)

    # -- classification -------------------------------------------------------

    def _classify(self, spec: SignalSpec, ev: SignalEvidence) -> Finding:
        during = ev.windows.get("during")
        has_incident_data = during is not None and during.has_data

        if not has_incident_data:
            classification = "no_data"
            confidence = 0.0
            note = (
                "Optional signal — no series available."
                if spec.optional
                else "No data in the incident window; metric may be unavailable."
            )
            return Finding(
                title=f"{spec.description} — no data",
                category=spec.category,
                classification=classification,
                confidence=confidence,
                evidence=note,
                signal=spec.name,
            )

        if spec.strategy == "absolute":
            return self._classify_absolute(spec, ev)
        if spec.strategy == "count":
            return self._classify_count(spec, ev)
        return self._classify_ratio(spec, ev)

    def _classify_absolute(self, spec: SignalSpec, ev: SignalEvidence) -> Finding:
        value = ev.incident_value or 0.0
        crit = spec.critical if spec.critical is not None else 95.0
        warn = spec.warn if spec.warn is not None else 80.0
        detail = (
            f"Incident-window {spec.aggregate} = {value:.1f}{spec.unit} "
            f"(warn {warn:.0f}, critical {crit:.0f}); baseline "
            f"{self._fmt(ev.baseline_value)}{spec.unit}."
        )
        if value >= crit:
            return self._finding(spec, "confirmed", 0.9, detail)
        if value >= warn:
            return self._finding(spec, "probable", 0.6, detail)
        if value >= warn * 0.85:
            return self._finding(spec, "possible", 0.35, detail)
        return self._finding(spec, "false_positive", 0.1, detail)

    def _classify_count(self, spec: SignalSpec, ev: SignalEvidence) -> Finding:
        incident = ev.incident_value or 0.0
        baseline = ev.baseline_value or 0.0
        detail = (
            f"Up to {incident:.0f} restart(s) in a 5m window during the incident; "
            f"baseline peak {baseline:.0f}."
        )
        if incident >= 1 and baseline < 1:
            return self._finding(spec, "confirmed", 0.85, detail)
        if incident >= 1 and baseline >= 1:
            return self._finding(spec, "possible", 0.4, detail + " Restarts also occur at baseline (possibly chronic).")
        return self._finding(spec, "false_positive", 0.05, "No restarts observed in the incident window.")

    def _classify_ratio(self, spec: SignalSpec, ev: SignalEvidence) -> Finding:
        s = self.settings
        baseline = ev.baseline_value
        incident = ev.incident_value or 0.0

        if baseline is None:
            # Incident data exists but no baseline to compare against.
            detail = (
                f"Incident-window {spec.aggregate} = {incident:.3g}{spec.unit}, "
                "but no baseline data to compare against."
            )
            return self._finding(spec, "possible", 0.3, detail)

        ratio = ev.ratio
        if ratio is None:  # baseline ~0 and incident ~0
            ratio = 1.0 if incident <= 1e-9 else float("inf")
        is_inf = ratio == float("inf") or (baseline <= 1e-9 < incident)
        ratio_text = "∞ (from ~0)" if is_inf else f"{ratio:.2f}x"
        detail = (
            f"Incident {spec.aggregate} {incident:.3g}{spec.unit} vs baseline "
            f"{self._fmt(baseline)}{spec.unit} = {ratio_text}."
        )

        effective = float("inf") if is_inf else ratio
        if effective >= s.anomaly_confirm_ratio:
            conf = 0.9 if is_inf else min(0.95, 0.5 + (effective - s.anomaly_confirm_ratio) * 0.1 + 0.3)
            return self._finding(spec, "confirmed", min(conf, 0.95), detail)
        if effective >= s.anomaly_probable_ratio:
            return self._finding(spec, "probable", 0.6, detail)
        if effective >= s.anomaly_possible_ratio:
            return self._finding(spec, "possible", 0.35, detail)
        return self._finding(
            spec,
            "false_positive",
            0.1,
            detail + " Within normal baseline variation.",
        )

    def _finding(
        self, spec: SignalSpec, classification: str, confidence: float, evidence: str
    ) -> Finding:
        verb = {
            "confirmed": "confirmed anomaly",
            "probable": "probable anomaly",
            "possible": "possible anomaly",
            "false_positive": "no significant change",
            "no_data": "no data",
        }[classification]
        return Finding(
            title=f"{spec.description} — {verb}",
            category=spec.category,
            classification=classification,
            confidence=round(confidence, 2),
            evidence=evidence,
            signal=spec.name,
        )

    @staticmethod
    def _fmt(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value:.3g}"

    # -- causality, correlation, scoring -------------------------------------

    def _assign_causality(self, findings: List[Finding]) -> None:
        active = [f for f in findings if f.classification in ("confirmed", "probable")]
        if not active:
            return
        ranked = sorted(active, key=lambda f: _CAUSE_RANK.get(f.category, 99))
        primary_rank = _CAUSE_RANK.get(ranked[0].category, 99)
        for f in active:
            rank = _CAUSE_RANK.get(f.category, 99)
            if rank == primary_rank:
                f.causality = "primary_cause"
            else:
                f.causality = "downstream_effect"

    def _correlate(
        self, findings: List[Finding], signals: List[SignalEvidence]
    ) -> List[str]:
        notes: List[str] = []
        active = {
            f.category
            for f in findings
            if f.classification in ("confirmed", "probable")
        }

        if "restart" in active and ("error_rate" in active or "latency" in active):
            notes.append(
                "Container restarts coincide with elevated error rate/latency — the "
                "errors are likely a downstream effect of crashes or a deploy, not the "
                "root cause."
            )
        if "saturation" in active and "restart" in active:
            notes.append(
                "Memory/resource saturation alongside restarts is consistent with "
                "OOM-kill behaviour (saturation is the likely primary cause)."
            )
        if "request_volume" in active and ("latency" in active or "cpu" in active):
            notes.append(
                "Request volume rose together with latency/CPU — the incident may be "
                "load-driven rather than a code or infrastructure fault."
            )
        if "error_rate" in active and "latency" in active and "restart" not in active and "saturation" not in active:
            notes.append(
                "Error rate and latency both rose with no resource or restart signal — "
                "points to an application-level or dependency fault."
            )
        if "dependency" in active:
            notes.append(
                "Dependency call failures are elevated — investigate upstream services "
                "before concluding the fault is local."
            )
        if not notes:
            notes.append(
                "No multi-signal correlation detected; signals do not reinforce a single "
                "narrative, which weakens any root-cause claim."
            )
        return notes

    def _missing_signals(
        self,
        specs: List[SignalSpec],
        findings: List[Finding],
    ) -> List[str]:
        missing: List[str] = []
        for spec, finding in zip(specs, findings):
            if finding.classification == "no_data":
                label = f"{spec.description} ({spec.category})"
                if spec.optional:
                    label += " — instrument this metric for fuller analysis"
                missing.append(label)
        return missing

    def _score(self, findings: List[Finding]) -> Tuple[float, bool]:
        score = sum(_CLASS_WEIGHT.get(f.classification, 0.0) for f in findings)
        confirmed = sum(1 for f in findings if f.classification == "confirmed")
        probable = sum(1 for f in findings if f.classification == "probable")
        sufficient = score >= self.settings.min_evidence_score and (
            confirmed >= 1 or probable >= 2
        )
        return score, sufficient

    def _root_cause_hypothesis(self, findings: List[Finding]) -> Optional[str]:
        primary = [f for f in findings if f.causality == "primary_cause"]
        if not primary:
            return None
        best = max(primary, key=lambda f: f.confidence)
        return f"{best.title} (confidence {best.confidence:.0%}). {best.evidence}"

    def _next_steps(
        self, findings: List[Finding], missing: List[str], sufficient: bool
    ) -> List[str]:
        steps: List[str] = []
        if not sufficient:
            steps.append(
                "Evidence is insufficient for a confident root cause — do NOT raise a "
                "formal incident on these signals alone."
            )
            steps.append(
                "Widen the analysis window and re-run, or correlate with application "
                "traces and deployment history around the alert time."
            )
        if missing:
            steps.append(
                "Instrument the missing signals so future analysis can correlate them: "
                + "; ".join(m.split(" — ")[0] for m in missing[:6])
                + "."
            )
        possibles = [f for f in findings if f.classification == "possible"]
        if possibles and not sufficient:
            steps.append(
                "Manually verify the 'possible' signals: "
                + ", ".join(p.category for p in possibles[:5])
                + "."
            )
        if not steps:
            steps.append(
                "Confirm the root-cause hypothesis against deployment/config change "
                "history before remediation."
            )
        return steps
