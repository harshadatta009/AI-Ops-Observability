from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


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

