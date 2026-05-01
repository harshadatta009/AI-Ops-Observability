from datetime import datetime, timezone
from typing import Any, Dict

import requests

from .models import AlertContext


SERVICE_LABEL = "container_label_com_docker_swarm_service_name"
DB_SERVICE_REGEX = ".*(postgres|influx|redis|trino|minio|mysql|mongo|db).*"
ERROR_LOG_PATTERN = (
    "(?i)error|exception|failed|timeout|oom|killed|critical|panic|refused|"
    "disk|memory|cpu|connection reset|context canceled"
)


class PrometheusClient:
    def __init__(self, base_url: str, timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def query(self, query: str) -> Any:
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/query",
                params={"query": query},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.json().get("data", {}).get("result", [])
        except Exception as exc:
            return {"error": str(exc), "query": query}


class LokiClient:
    def __init__(
        self,
        base_url: str,
        max_log_lines: int,
        max_log_chars: int,
        timeout_seconds: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_log_lines = max_log_lines
        self.max_log_chars = max_log_chars
        self.timeout_seconds = timeout_seconds

    def query_range(self, logql: str, lookback_minutes: int) -> Dict[str, Any]:
        end_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        start_ns = end_ns - lookback_minutes * 60 * 1_000_000_000

        try:
            response = requests.get(
                f"{self.base_url}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "start": start_ns,
                    "end": end_ns,
                    "limit": self.max_log_lines,
                    "direction": "backward",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            streams = response.json().get("data", {}).get("result", [])
            lines = []
            for stream in streams:
                labels = stream.get("stream", {})
                for _, line in stream.get("values", []):
                    lines.append(f"{labels} {line}")

            return {
                "query": logql,
                "logs": "\n".join(lines[: self.max_log_lines])[: self.max_log_chars],
            }
        except Exception as exc:
            return {"error": str(exc), "query": logql, "logs": ""}


class ObservabilityCollector:
    def __init__(
        self,
        prometheus: PrometheusClient,
        loki: LokiClient,
        lookback_minutes: int,
    ) -> None:
        self.prometheus = prometheus
        self.loki = loki
        self.lookback_minutes = lookback_minutes

    def alert_metrics(self, ctx: AlertContext) -> Dict[str, Any]:
        label_selector = self._alert_label_selector(ctx)
        queries = {
            "container_cpu_percent": (
                "100 * sum by (container_label_com_docker_swarm_service_name, name) "
                f"(rate(container_cpu_usage_seconds_total{label_selector}[5m]))"
            ),
            "container_memory_mb": (
                "sum by (container_label_com_docker_swarm_service_name, name) "
                f"(container_memory_usage_bytes{label_selector}) / 1024 / 1024"
            ),
            "container_restarts": (
                "sum by (container_label_com_docker_swarm_service_name, name) "
                f"(changes(container_start_time_seconds{label_selector}[{self.lookback_minutes}m]))"
            ),
            "host_cpu_percent": (
                '100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
            ),
        }
        return {name: self.prometheus.query(query) for name, query in queries.items()}

    def alert_logs(self, ctx: AlertContext) -> Dict[str, Any]:
        label_parts = []
        if ctx.service_name:
            label_parts.append(f'service_name="{ctx.service_name}"')
        elif ctx.container_name:
            label_parts.append(f'container_name=~".*{ctx.container_name}.*"')

        selector = "{" + ",".join(label_parts) + "}" if label_parts else '{service_name=~".+"}'
        logql = selector + ' |~ "(?i)error|exception|failed|timeout|oom|killed|critical|panic|refused"'
        return self.loki.query_range(logql, self.lookback_minutes)

    def system_metrics(self) -> Dict[str, Any]:
        cadvisor_filter = f'job="cadvisor",{SERVICE_LABEL}!=""'
        cpu_metric = f"rate(container_cpu_usage_seconds_total{{{cadvisor_filter}}}[5m])"
        memory_metric = f"container_memory_usage_bytes{{{cadvisor_filter}}}"
        restart_metric = f"changes(container_start_time_seconds{{{cadvisor_filter}}}[24h])"
        cpu_by_service = "100 * sum by (service_name) (" + self._with_service_name(cpu_metric) + ")"
        memory_by_service = "sum by (service_name) (" + self._with_service_name(memory_metric) + ") / 1024 / 1024"

        queries = {
            "top_cpu_services_24h_avg_percent": f"topk(10, avg_over_time(({cpu_by_service})[24h:5m]))",
            "top_cpu_services_24h_peak_percent": f"topk(10, max_over_time(({cpu_by_service})[24h:5m]))",
            "top_memory_services_24h_avg_mb": f"topk(10, avg_over_time(({memory_by_service})[24h:5m]))",
            "top_memory_services_24h_peak_mb": f"topk(10, max_over_time(({memory_by_service})[24h:5m]))",
            "host_cpu_percent_current": '100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
            "host_cpu_percent_24h_peak": (
                'max_over_time((100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100))[24h:5m])'
            ),
            "host_memory_percent_current": "100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))",
            "host_memory_percent_24h_peak": (
                "max_over_time((100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)))[24h:5m])"
            ),
            "container_restarts_24h": (
                "topk(10, sum by (service_name) (" + self._with_service_name(restart_metric) + "))"
            ),
            "filesystem_used_percent_current": (
                '100 * (1 - (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay",mountpoint!~"/run.*|/var/lib/docker/.*"} '
                '/ node_filesystem_size_bytes{fstype!~"tmpfs|overlay",mountpoint!~"/run.*|/var/lib/docker/.*"}))'
            ),
        }
        return {name: self.prometheus.query(query) for name, query in queries.items()}

    def db_metrics(self) -> Dict[str, Any]:
        db_filter = f'job="cadvisor",{SERVICE_LABEL}=~"{DB_SERVICE_REGEX}"'
        db_cpu_metric = f"rate(container_cpu_usage_seconds_total{{{db_filter}}}[5m])"
        db_memory_metric = f"container_memory_usage_bytes{{{db_filter}}}"
        db_restart_metric = f"changes(container_start_time_seconds{{{db_filter}}}[24h])"
        db_cpu_by_service = "100 * sum by (service_name) (" + self._with_service_name(db_cpu_metric) + ")"
        db_memory_by_service = "sum by (service_name) (" + self._with_service_name(db_memory_metric) + ") / 1024 / 1024"

        queries = {
            "db_cpu_services_24h_avg_percent": f"topk(10, avg_over_time(({db_cpu_by_service})[24h:5m]))",
            "db_cpu_services_24h_peak_percent": f"topk(10, max_over_time(({db_cpu_by_service})[24h:5m]))",
            "db_memory_services_24h_avg_mb": f"topk(10, avg_over_time(({db_memory_by_service})[24h:5m]))",
            "db_memory_services_24h_peak_mb": f"topk(10, max_over_time(({db_memory_by_service})[24h:5m]))",
            "db_restarts_24h": (
                "topk(10, sum by (service_name) (" + self._with_service_name(db_restart_metric) + "))"
            ),
            "filesystem_used_percent_current": (
                '100 * (1 - (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay",mountpoint!~"/run.*|/var/lib/docker/.*"} '
                '/ node_filesystem_size_bytes{fstype!~"tmpfs|overlay",mountpoint!~"/run.*|/var/lib/docker/.*"}))'
            ),
        }
        return {name: self.prometheus.query(query) for name, query in queries.items()}

    def logs(self, db_only: bool = False) -> Dict[str, Any]:
        if db_only:
            selector = '{service_name=~".*(postgres|influx|redis|trino|minio|mysql|mongo|db).*"}'
        else:
            selector = '{service_name=~".+"}'
        return self.loki.query_range(selector + f' |~ "{ERROR_LOG_PATTERN}"', 24 * 60)

    @staticmethod
    def _with_service_name(metric_expr: str) -> str:
        return (
            f'label_replace({metric_expr}, "service_name", "$1", '
            f'"{SERVICE_LABEL}", "(.+)")'
        )

    @staticmethod
    def _alert_label_selector(ctx: AlertContext) -> str:
        if ctx.service_name:
            return f'{{{SERVICE_LABEL}=~"{ctx.service_name}"}}'
        if ctx.container_name:
            return f'{{name=~".*{ctx.container_name}.*"}}'
        return ""
