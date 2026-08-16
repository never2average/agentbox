"""
AutoScaler Reconcilers
Read the declared signals, decide a replica count, and move the target.

All three autoscaler kinds share this logic; they differ only in the kind they
own and one specialised block.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from kubernetes import client

from controller import status
from controller.context import Context, logger
from controller.metrics import MetricSource

# Matches the HorizontalPodAutoscaler default: ignore deviations under 10% so a
# metric hovering near its target does not cause continuous churn.
DEFAULT_TOLERANCE = 0.1

TARGET_PLURALS = {
    "Model": "models",
    "HarnessRuntime": "harnessruntimes",
    "ToolServer": "toolservers",
}


def _desired_for_metric(metric: Dict[str, Any], current: int, observed: float,
                        tolerance: float = DEFAULT_TOLERANCE) -> Optional[int]:
    """
    Replica count this one metric argues for.

    Follows the HorizontalPodAutoscaler formula:
    desired = ceil(current * observed / target), with a tolerance band around
    the target so small deviations do not move anything.

    Args:
        metric: A scalingMetric entry
        current: Current replica count
        observed: Observed metric value
        tolerance: Relative deviation to ignore

    Returns:
        Desired replicas, or None when the metric cannot decide
    """
    target = metric.get("target", {})
    wanted = target.get("value")
    if not wanted:
        return None

    kind = target.get("metricType", "averageValue")

    # Activation: at zero replicas there is no per-replica average to reason
    # about, so any demand at all brings one replica back. Scaling proper
    # resumes from there.
    if current == 0:
        return 1 if observed > 0 else 0

    base = current

    if kind == "value":
        # observed is a total and the target is what one replica handles
        ratio = (observed / wanted) / base
    else:
        # utilization and averageValue are both per-replica readings
        ratio = observed / wanted

    if abs(ratio - 1.0) <= tolerance:
        return current

    # No floor here: bounds decide whether zero is allowed, and a floor of 1 at
    # this level would make scaleToZero unreachable.
    return max(0, int(-(-(base * ratio) // 1)))


def _observe(source: MetricSource, metric: Dict[str, Any], namespace: str,
             selector: Optional[str]) -> Optional[float]:
    """Read the current value of a scaling metric."""
    kind = metric.get("type")
    if kind == "aiMetric":
        return source.value(metric["metric"], namespace)
    if kind == "resource":
        return source.resource_value(metric["resource"], namespace, selector or "")
    if kind == "external":
        return source.value(metric["name"], namespace)
    return None


def _seconds_since(timestamp: Optional[str]) -> Optional[float]:
    """Seconds elapsed since an RFC 3339 timestamp, or None if it cannot be read."""
    if not timestamp:
        return None
    try:
        previous = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - previous).total_seconds()


def _apply_policies(rules: Dict[str, Any], current: int, desired: int,
                    elapsed: Optional[float]) -> Tuple[int, str]:
    """
    Rate-limit a scaling decision with spec.behavior.<direction>.policies.

    A policy caps how much one scaling event may move: `pods` is an absolute
    step, `percent` is relative to the current count. `selectPolicy` decides
    which cap wins — `max` is the most permissive, `min` the most conservative,
    `disabled` blocks movement in that direction entirely. `periodSeconds` is
    the minimum interval between events under that policy.

    Args:
        rules: The scaleUp or scaleDown block
        current: Current replica count
        desired: Replica count the metrics argue for
        elapsed: Seconds since the last scale, or None if there has not been one

    Returns:
        (allowed replica count, reason when the decision was limited)
    """
    if rules.get("selectPolicy") == "disabled":
        return current, "scaling is disabled in this direction by selectPolicy"

    policies = rules.get("policies")
    if not policies:
        return desired, ""

    ready = [p for p in policies
             if elapsed is None or elapsed >= p.get("periodSeconds", 0)]
    if not ready:
        soonest = min(p.get("periodSeconds", 0) for p in policies)
        return current, (f"policy period: {int(soonest - (elapsed or 0))}s until the next "
                         f"change is allowed")

    caps = []
    for policy in ready:
        value = policy["value"]
        if policy["type"] == "pods":
            caps.append(value)
        else:
            caps.append(max(1, int(current * value / 100)))

    cap = min(caps) if rules.get("selectPolicy") == "min" else max(caps)
    delta = desired - current

    if abs(delta) <= cap:
        return desired, ""

    limited = current + (cap if delta > 0 else -cap)
    return limited, f"policy caps this change at {cap} replicas per event"


def _within_stabilization(resource: Dict[str, Any], behavior: Dict[str, Any],
                          desired: int, current: int) -> Tuple[bool, str]:
    """
    Whether a stabilization window blocks this change.

    Args:
        resource: The autoscaler
        behavior: spec.behavior
        desired: Replica count we want
        current: Replica count we have

    Returns:
        (blocked, reason)
    """
    last = (resource.get("status") or {}).get("lastScaleTime")
    if not last or desired == current:
        return False, ""

    rules = behavior.get("scaleUp" if desired > current else "scaleDown", {})
    window = rules.get("stabilizationWindowSeconds")
    if not window:
        return False, ""

    try:
        previous = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False, ""

    remaining = (previous + timedelta(seconds=window)) - datetime.now(timezone.utc)
    if remaining.total_seconds() > 0:
        return True, f"stabilization window: {int(remaining.total_seconds())}s remaining"
    return False, ""


def reconcile_autoscaler(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scale an autoscaler's target to match its metrics.

    Works for ModelAutoScaler, HarnessSwarmAutoScaler and ToolServerAutoScaler.
    """
    meta, spec = resource["metadata"], resource["spec"]
    namespace = meta["namespace"]
    target_ref = spec["scaleTargetRef"]
    plural = TARGET_PLURALS.get(target_ref["kind"])
    bounds = spec.get("bounds", {})
    floor, ceiling = bounds.get("minReplicas", 1), bounds["maxReplicas"]

    if not spec.get("enabled", True):
        return status.build(resource, "suspended", "spec.enabled is false",
                            [status.condition(status.READY, False, "Disabled")])

    target = ctx.get_resource(plural, namespace, target_ref["name"]) if plural else None
    if target is None:
        return status.build(
            resource, "degraded", f"target {target_ref['kind']}/{target_ref['name']} not found",
            [status.condition(status.READY, False, "TargetNotFound")])

    current = target.get("spec", {}).get("replicas", 1)
    selector = (target.get("status") or {}).get("selector")
    source = MetricSource(ctx)

    proposals: List[int] = []
    observations: List[Dict[str, Any]] = []
    for metric in spec.get("metrics", []):
        observed = _observe(source, metric, namespace, selector)
        label = metric.get("metric") or metric.get("resource") or metric.get("name")
        if observed is None:
            observations.append({"metric": label, "observed": None, "desiredReplicas": None})
            continue
        proposal = _desired_for_metric(metric, current, observed,
                                       spec.get("tolerance", DEFAULT_TOLERANCE))
        observations.append({"metric": label, "observed": observed,
                             "desiredReplicas": proposal})
        if proposal is not None:
            proposals.append(proposal)

    if not proposals:
        return status.build(
            resource, "pending", "no metric values available yet",
            [status.condition(status.READY, False, "NoMetrics")],
            currentReplicas=current, metrics=observations)

    desired = max(proposals)
    scale_to_zero = spec.get("behavior", {}).get("scaleToZero", {})
    lower = 0 if scale_to_zero.get("enabled") else max(floor, 1)
    desired = max(lower, min(ceiling, desired))

    behavior = spec.get("behavior", {})
    elapsed = _seconds_since((resource.get("status") or {}).get("lastScaleTime"))
    if desired != current:
        direction = behavior.get("scaleUp" if desired > current else "scaleDown", {})
        desired, limit_reason = _apply_policies(direction, current, desired, elapsed)
        if limit_reason and desired == current:
            return status.build(
                resource, "active", limit_reason,
                [status.condition(status.READY, True, "RateLimited")],
                currentReplicas=current, desiredReplicas=desired, metrics=observations,
                lastScaleTime=(resource.get("status") or {}).get("lastScaleTime"))

    blocked, reason = _within_stabilization(resource, behavior, desired, current)
    if blocked:
        return status.build(
            resource, "active", reason,
            [status.condition(status.READY, True, "Stabilizing")],
            currentReplicas=current, desiredReplicas=desired, metrics=observations,
            lastScaleTime=(resource.get("status") or {}).get("lastScaleTime"))

    if desired == current:
        return status.build(
            resource, "active", f"holding at {current} replicas",
            [status.condition(status.READY, True, "AtTarget")],
            currentReplicas=current, desiredReplicas=desired, metrics=observations,
            lastScaleTime=(resource.get("status") or {}).get("lastScaleTime"))

    ctx.patch_scale(plural, namespace, target_ref["name"], desired)
    ctx.event(resource, "Scaled",
              f"{target_ref['kind']}/{target_ref['name']} {current} -> {desired}")
    logger.info("scaled %s/%s from %d to %d", target_ref["kind"], target_ref["name"],
                current, desired)

    return status.build(
        resource, "active", f"scaled {target_ref['name']} from {current} to {desired}",
        [status.condition(status.READY, True, "Scaled")],
        currentReplicas=desired, desiredReplicas=desired, metrics=observations,
        lastScaleTime=status.now())
