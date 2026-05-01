from typing import Any, Dict, List

from .models import AlertContext


class GrafanaAlertParser:
    def parse(self, payload: Dict[str, Any]) -> List[AlertContext]:
        alerts = payload.get("alerts") or []
        if alerts:
            return [self._from_alert(payload, alert) for alert in alerts]

        common_labels = payload.get("commonLabels", {}) or {}
        common_annotations = payload.get("commonAnnotations", {}) or {}
        return [
            AlertContext(
                alertname=common_labels.get("alertname") or payload.get("title") or "UnknownAlert",
                status=payload.get("status", "unknown"),
                severity=common_labels.get("severity", "unknown"),
                service_name=self._service_name(common_labels),
                container_name=self._container_name(common_labels),
                instance=common_labels.get("instance"),
                summary=common_annotations.get("summary") or payload.get("message"),
                description=common_annotations.get("description"),
                starts_at=payload.get("startsAt"),
                raw_labels=common_labels,
                raw_annotations=common_annotations,
            )
        ]

    def _from_alert(self, payload: Dict[str, Any], alert: Dict[str, Any]) -> AlertContext:
        labels = alert.get("labels", {}) or {}
        annotations = alert.get("annotations", {}) or {}
        return AlertContext(
            alertname=labels.get("alertname") or payload.get("title") or "UnknownAlert",
            status=alert.get("status") or payload.get("status") or "unknown",
            severity=labels.get("severity", "unknown"),
            service_name=self._service_name(labels),
            container_name=self._container_name(labels),
            instance=labels.get("instance"),
            summary=annotations.get("summary") or payload.get("message"),
            description=annotations.get("description"),
            starts_at=alert.get("startsAt"),
            raw_labels=labels,
            raw_annotations=annotations,
        )

    @staticmethod
    def _service_name(labels: Dict[str, Any]) -> str | None:
        return labels.get("service_name") or labels.get("service") or labels.get("job")

    @staticmethod
    def _container_name(labels: Dict[str, Any]) -> str | None:
        return labels.get("container_name") or labels.get("container") or labels.get("name")

