"""
Metric Source
Where autoscalers, meters and guardrails read their numbers from.

Two backends, tried in order:

  1. Prometheus, when AGENTBOX_PROMETHEUS_URL is set — the real path
  2. The `agentbox-metrics` ConfigMap in the resource's namespace, mapping
     metric name to value — the path that works before a metrics stack exists,
     and the one an operator can use to pin a value by hand

Resource metrics (cpu, memory, gpu) fall back to the metrics.k8s.io API.
"""
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from kubernetes import client

from controller.context import Context, logger

METRICS_CONFIG_MAP = "agentbox-metrics"


class MetricSource:
    """Reads metric values for a namespace."""

    def __init__(self, ctx: Context):
        """
        Args:
            ctx: Controller context
        """
        self.ctx = ctx

    def value(self, name: str, namespace: str) -> Optional[float]:
        """
        Look up the current value of a named metric.

        Args:
            name: AIMetric name
            namespace: Namespace to look in

        Returns:
            The value, or None when no source can supply it
        """
        if self.ctx.prometheus_url:
            found = self._from_prometheus(name)
            if found is not None:
                return found
        return self._from_config_map(name, namespace)

    def values_by_dimension(self, name: str, namespace: str,
                            dimensions: list) -> Dict[str, float]:
        """
        Break a metric down by the dimensions a meter attributes usage to.

        Prometheus is queried with `sum by (dimensions) (metric)`. The ConfigMap
        backend uses keys of the form `metric.dimension.value`, since ConfigMap
        keys allow only alphanumerics, dashes, underscores and dots.

        Args:
            name: AIMetric name
            namespace: Namespace to look in
            dimensions: Dimension names to break down by

        Returns:
            Mapping of dimension-value label to value; empty when unavailable
        """
        if not dimensions:
            return {}

        if self.ctx.prometheus_url:
            found = self._prometheus_by_dimension(name, dimensions)
            if found:
                return found

        try:
            cm = self.ctx.core.read_namespaced_config_map(METRICS_CONFIG_MAP, namespace)
        except client.exceptions.ApiException:
            return {}

        breakdown = {}
        for key, raw in (cm.data or {}).items():
            match = re.match(rf"^{re.escape(name)}\.([^.]+)\.(.+)$", key)
            if not match or match.group(1) not in dimensions:
                continue
            try:
                breakdown[f"{match.group(1)}={match.group(2)}"] = float(raw)
            except ValueError:
                continue
        return breakdown

    def _prometheus_by_dimension(self, name: str, dimensions: list) -> Dict[str, float]:
        """Query Prometheus for a metric grouped by dimensions."""
        by = ",".join(dimensions)
        query = urllib.parse.quote(f"sum by ({by}) ({name})")
        url = f"{self.ctx.prometheus_url.rstrip('/')}/api/v1/query?query={query}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read())
        except Exception as e:  # noqa: BLE001
            logger.debug("prometheus breakdown for %s failed: %s", name, e)
            return {}

        breakdown = {}
        for result in payload.get("data", {}).get("result", []):
            labels = result.get("metric", {})
            key = ",".join(f"{d}={labels.get(d, '')}" for d in dimensions)
            try:
                breakdown[key] = float(result["value"][1])
            except (KeyError, IndexError, ValueError):
                continue
        return breakdown

    def resource_value(self, resource: str, namespace: str, selector: str) -> Optional[float]:
        """
        Average utilisation for a resource metric across the selected pods.

        Args:
            resource: "cpu", "memory", "gpu" or "gpuMemory"
            namespace: Namespace to look in
            selector: Label selector identifying the pods

        Returns:
            Average utilisation percentage, or None when metrics are unavailable
        """
        override = self._from_config_map(f"resource:{resource}", namespace)
        if override is not None:
            return override

        if resource not in ("cpu", "memory"):
            return None

        try:
            metrics = self.ctx.custom.list_namespaced_custom_object(
                "metrics.k8s.io", "v1beta1", namespace, "pods",
                label_selector=selector)
        except client.exceptions.ApiException:
            return None

        totals = []
        for pod in metrics.get("items", []):
            for container in pod.get("containers", []):
                raw = container.get("usage", {}).get(resource)
                if raw is not None:
                    totals.append(_parse_quantity(raw))
        if not totals:
            return None
        return sum(totals) / len(totals)

    # ------------------------------------------------------------------ backends
    def _from_prometheus(self, name: str) -> Optional[float]:
        """Query Prometheus for the latest value of a metric."""
        query = urllib.parse.quote(name)
        url = f"{self.ctx.prometheus_url.rstrip('/')}/api/v1/query?query={query}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read())
        except Exception as e:  # noqa: BLE001 - any failure means "fall through"
            logger.debug("prometheus lookup for %s failed: %s", name, e)
            return None

        results = payload.get("data", {}).get("result", [])
        if not results:
            return None
        try:
            return float(results[0]["value"][1])
        except (KeyError, IndexError, ValueError):
            return None

    def _from_config_map(self, name: str, namespace: str) -> Optional[float]:
        """Read a pinned value out of the agentbox-metrics ConfigMap."""
        try:
            cm = self.ctx.core.read_namespaced_config_map(METRICS_CONFIG_MAP, namespace)
        except client.exceptions.ApiException:
            return None
        raw = (cm.data or {}).get(name)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            logger.warning("metric %s in %s is not a number: %r", name, METRICS_CONFIG_MAP, raw)
            return None


def _parse_quantity(raw: str) -> float:
    """
    Parse a Kubernetes quantity into a float.

    Args:
        raw: Quantity string, e.g. "150m", "64Mi", "1"

    Returns:
        Numeric value; CPU in millicores, memory in mebibytes
    """
    suffixes = {"n": 1e-6, "u": 1e-3, "m": 1.0, "Ki": 1 / 1024, "Mi": 1.0,
                "Gi": 1024.0, "Ti": 1024 * 1024, "k": 1e-3, "M": 1.0, "G": 1024.0}
    for suffix, factor in sorted(suffixes.items(), key=lambda kv: -len(kv[0])):
        if raw.endswith(suffix):
            try:
                return float(raw[:-len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(raw) * 1000  # bare CPU cores -> millicores
    except ValueError:
        return 0.0
