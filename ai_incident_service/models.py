from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class GrafanaWebhook(BaseModel):
    """Schema-validated Grafana webhook envelope.

    Permissive (extra fields allowed) so real Grafana/Alertmanager payload
    variations are not rejected, but it enforces that ``alerts`` is a list and
    the top-level shape is an object — stopping malformed/garbage POST bodies
    before they reach analysis.
    """

    model_config = ConfigDict(extra="allow")

    status: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    alerts: Optional[List[Dict[str, Any]]] = None
    commonLabels: Optional[Dict[str, Any]] = None  # noqa: N815 (Grafana field name)
    commonAnnotations: Optional[Dict[str, Any]] = None  # noqa: N815


class AlertContext(BaseModel):
    alertname: str = "UnknownAlert"
    status: str = "unknown"
    severity: str = "unknown"
    service_name: Optional[str] = None
    container_name: Optional[str] = None
    instance: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    starts_at: Optional[str] = None
    raw_labels: Dict[str, Any] = Field(default_factory=dict)
    raw_annotations: Dict[str, Any] = Field(default_factory=dict)


# ----- Deep incident analysis models -----------------------------------------


class AnalysisWindow(BaseModel):
    """A named time range used for before/during/after comparison."""

    name: str  # baseline | pre | during | post
    start: str  # ISO 8601
    end: str  # ISO 8601
    description: str = ""


class SignalStats(BaseModel):
    """Aggregated statistics for one signal within one window."""

    has_data: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    avg: Optional[float] = None
    p95: Optional[float] = None
    last: Optional[float] = None
    sample_count: int = 0
    series_count: int = 0


class SignalEvidence(BaseModel):
    """A single signal evaluated across windows, with the query that produced it."""

    name: str
    category: str  # error_rate | latency | request_volume | saturation | cpu | ...
    description: str
    query: str
    unit: str = ""
    optional: bool = False  # missing data is expected/acceptable for optional signals
    windows: Dict[str, SignalStats] = Field(default_factory=dict)
    baseline_value: Optional[float] = None
    incident_value: Optional[float] = None
    ratio: Optional[float] = None  # incident_value / baseline_value
    delta: Optional[float] = None  # incident_value - baseline_value


class Finding(BaseModel):
    """A classified conclusion about one signal, with confidence and causality."""

    title: str
    category: str
    # confirmed | probable | possible | false_positive | no_data
    classification: str
    confidence: float  # 0.0 - 1.0
    causality: str = "undetermined"  # primary_cause | downstream_effect | undetermined
    evidence: str
    signal: Optional[str] = None


class EvidenceBundle(BaseModel):
    """The full structured result of deep analysis, fed to the report generator."""

    incident_time: str
    target: str
    windows: List[AnalysisWindow] = Field(default_factory=list)
    signals: List[SignalEvidence] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    correlations: List[str] = Field(default_factory=list)
    root_cause_hypothesis: Optional[str] = None
    evidence_score: float = 0.0
    sufficient_evidence: bool = False
    missing_signals: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    query_errors: List[str] = Field(default_factory=list)

