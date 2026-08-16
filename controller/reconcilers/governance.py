"""
Governance and Observability Reconcilers
AgentIdP, Guardrail, AIMetric, AIMeter and Tracer.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client

from controller import children, status
from controller.context import Context, logger
from controller.metrics import MetricSource

def _seconds_since(timestamp: Optional[str]) -> Optional[float]:
    """Seconds elapsed since an RFC 3339 timestamp, or None if unreadable."""
    if not timestamp:
        return None
    try:
        previous = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - previous).total_seconds()


OPERATORS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,
}


# ------------------------------------------------------------------- AgentIdP
def reconcile_agent_idp(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Give agents an identity: a ServiceAccount, plus a Role and RoleBinding
    scoped to the AgentBox resources the policy groups mention.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "agent-idp")

    if spec.get("status") not in (None, "active"):
        return status.build(resource, "inactive", f"spec.status is {spec.get('status')}",
                            [status.condition(status.READY, False, "NotActive")])

    annotations = {}
    for identity in spec.get("identity", []):
        for key, value in (identity.get("annotations") or {}).items():
            annotations[key] = value

    children.ensure_service_account(ctx, client.V1ServiceAccount(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels,
                                     annotations=annotations or None,
                                     owner_references=[owner])))

    verbs = ["get", "list", "watch"]
    if spec.get("defaultBehavior") == "allow":
        verbs = ["get", "list", "watch", "create", "update", "patch"]

    children.ensure_role(ctx, client.V1Role(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels,
                                     owner_references=[owner]),
        rules=[
            client.V1PolicyRule(
                api_groups=["ai.agentbox.io"],
                resources=["toolservers", "gateways", "models", "datasets", "aimetrics"],
                verbs=verbs),
            client.V1PolicyRule(
                api_groups=[""], resources=["configmaps"], verbs=["get", "list", "watch"]),
        ]))

    children.ensure_role_binding(ctx, client.V1RoleBinding(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels,
                                     owner_references=[owner]),
        role_ref=client.V1RoleRef(api_group="rbac.authorization.k8s.io",
                                  kind="Role", name=name),
        subjects=[client.RbacV1Subject(kind="ServiceAccount", name=name,
                                       namespace=namespace)]))

    groups = spec.get("policyGroups", [])
    guardrails = sorted({g for group in groups for g in group.get("guardrails", [])})
    return status.build(
        resource, "active",
        f"identity {name} with {len(groups)} policy groups",
        [status.condition(status.READY, True, "IdentityProvisioned")],
        serviceAccount=name, role=name, guardrails=guardrails,
        defaultBehavior=spec.get("defaultBehavior", "deny"))


# ------------------------------------------------------------------- AIMetric
def reconcile_ai_metric(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish a metric definition, and report its current value when one is available.

    The definition is rendered as a Prometheus recording rule so an existing
    metrics stack can pick it up without knowing anything about AgentBox.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "ai-metric")

    if spec.get("status") == "inactive":
        return status.build(resource, "inactive", "spec.status is inactive",
                            [status.condition(status.READY, False, "NotActive")])

    metric_name = spec["metricName"]
    expression = spec.get("metricMath", {}).get("expression") if spec.get("type") == "metricMath" \
        else None
    rule = {
        "groups": [{
            "name": f"agentbox-{name}",
            "interval": f"{spec.get('periodSeconds', 60)}s",
            "rules": [{
                "record": f"{spec.get('namespace', 'agentbox')}:{metric_name}",
                "expr": expression or metric_name,
                "labels": {d.get("name", ""): d.get("value", "")
                           for d in spec.get("dimensions", []) if d.get("name")},
            }],
        }]
    }
    children.ensure_config_map(ctx, children.config_map(
        f"{name}-rule", namespace, {**labels, "prometheus": "agentbox"},
        {"rule.json": json.dumps(rule, indent=2)}, owner))

    observed = MetricSource(ctx).value(name, namespace)
    return status.build(
        resource, "active",
        f"{metric_name} ({spec.get('unit', 'None')})"
        + (f" = {observed}" if observed is not None else "; no value yet"),
        [status.condition(status.READY, True, "DefinitionPublished"),
         status.condition("HasValue", observed is not None,
                          "ValueObserved" if observed is not None else "NoSource")],
        ruleConfigMap=f"{name}-rule",
        metricName=metric_name,
        currentValue=observed)


# -------------------------------------------------------------------- AIMeter
def reconcile_ai_meter(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Turn a metric into usage, price it, and report the budget position.
    """
    meta, spec = resource["metadata"], resource["spec"]
    namespace = meta["namespace"]

    if not spec.get("enabled", True):
        return status.build(resource, "suspended", "spec.enabled is false",
                            [status.condition(status.READY, False, "Disabled")])

    usage = spec["usage"]
    source = MetricSource(ctx)
    observed = source.value(usage["source"]["metric"], namespace)
    if observed is None:
        return status.build(
            resource, "pending",
            f"no value for metric {usage['source']['metric']}",
            [status.condition(status.READY, False, "NoMetricValue")],
            unit=usage["unit"])

    pricing = spec.get("pricing", {})
    cost = None
    if pricing:
        per_units = pricing.get("perUnits", 1)
        if pricing.get("model") == "tiered":
            remaining, cost = observed, 0.0
            previous = 0.0
            for tier in sorted(pricing.get("tiers", []), key=lambda t: t["upTo"]):
                span = min(remaining, tier["upTo"] - previous)
                if span <= 0:
                    break
                cost += (span / per_units) * tier["unitPrice"]
                remaining -= span
                previous = tier["upTo"]
            if remaining > 0 and pricing.get("unitPrice"):
                cost += (remaining / per_units) * pricing["unitPrice"]
        elif pricing.get("unitPrice") is not None:
            cost = (observed / per_units) * pricing["unitPrice"]

    dimensions = spec.get("attribution", {}).get("dimensions", [])
    breakdown = []
    for label, amount in sorted(source.values_by_dimension(
            usage["source"]["metric"], namespace, dimensions).items()):
        entry = {"dimensions": label, "usage": amount}
        if pricing.get("unitPrice") is not None and pricing.get("model") != "tiered":
            entry["cost"] = (amount / pricing.get("perUnits", 1)) * pricing["unitPrice"]
        breakdown.append(entry)

    budget = spec.get("budget", {})
    limit = budget.get("limit")
    measured = cost if budget.get("limitType", "cost") == "cost" else observed
    exceeded = limit is not None and measured is not None and measured > limit
    used_percent = round(measured / limit * 100, 2) if limit and measured is not None else None

    conditions = [status.condition(status.READY, True, "Metered")]
    if limit is not None:
        conditions.append(status.condition(
            "WithinBudget", not exceeded,
            "BudgetExceeded" if exceeded else "WithinBudget",
            f"{used_percent}% of {limit} used" if used_percent is not None else ""))

    if exceeded:
        ctx.event(resource, "BudgetExceeded",
                  f"{measured} exceeds the {limit} limit; onExceed={budget.get('onExceed', 'alert')}",
                  "Warning")

    for threshold in sorted(budget.get("alertThresholdsPercent", [])):
        if used_percent is not None and used_percent >= threshold and not exceeded:
            ctx.event(resource, "BudgetThreshold",
                      f"{used_percent}% of the {limit} budget used (alert at {threshold}%)",
                      "Warning")

    return status.build(
        resource, "active",
        f"{observed} {usage['unit']}" + (f", {cost:.2f} {pricing.get('currency', 'USD')}"
                                         if cost is not None else ""),
        conditions,
        unit=usage["unit"], currentUsage=observed, currentCost=cost,
        budgetUsedPercent=used_percent,
        budgetExceeded=exceeded,
        attributedUsage=breakdown or None,
        window=spec["window"].get("period") or spec["window"].get("type"))


# ------------------------------------------------------------------ Guardrail
def _evaluate(condition: Dict[str, Any], source: MetricSource,
              namespace: str, seen: List[Dict[str, Any]]) -> Optional[bool]:
    """Evaluate one metric condition, recording what was observed."""
    metric = condition.get("metric")
    if not metric:
        return None
    observed = source.value(metric, namespace)
    threshold = condition.get("threshold")
    operator = OPERATORS.get(condition.get("operator", "gt"))
    seen.append({"metric": metric, "observed": observed, "threshold": threshold,
                 "operator": condition.get("operator", "gt")})
    if observed is None or threshold is None or operator is None:
        return None
    return operator(observed, threshold)


def reconcile_guardrail(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate a guardrail's conditions and report whether it is tripped.

    Enforcement of the effect itself belongs to whatever the guardrail scopes —
    the gateway throttles, the harness blocks. The controller decides *whether*
    it trips, records that in status, and emits an event so the rest of the
    platform can act on it.
    """
    meta, spec = resource["metadata"], resource["spec"]
    namespace = meta["namespace"]

    if spec.get("status") == "disabled":
        return status.build(resource, "inactive", "spec.status is disabled",
                            [status.condition(status.READY, False, "Disabled")])

    source = MetricSource(ctx)
    conditions_block = spec.get("conditions", {})
    observations: List[Dict[str, Any]] = []

    results = [_evaluate(c, source, namespace, observations)
               for c in conditions_block.get("all", [])]
    any_results = [_evaluate(c, source, namespace, observations)
                   for c in conditions_block.get("any", [])]

    known = [r for r in results + any_results if r is not None]
    if not known:
        return status.build(
            resource, "pending", "no metric values available to evaluate",
            [status.condition(status.READY, False, "NoMetrics")],
            observations=observations)

    tripped = (all(r for r in results if r is not None) if results else False)
    if any_results:
        tripped = tripped or any(r for r in any_results if r is not None)

    effect = spec.get("effect", {})
    previous = resource.get("status") or {}
    was_tripped = previous.get("triggered", False)
    last_fired = previous.get("lastFiredTime")
    since_fired = _seconds_since(last_fired)

    # suppressForSeconds keeps a guardrail quiet after it fires, so a flapping
    # metric does not produce a stream of trips
    suppress_for = spec.get("suppressForSeconds")
    suppressed = bool(suppress_for and since_fired is not None and since_fired < suppress_for)

    # cooldownSeconds is the minimum gap between two firings
    cooldown = spec.get("cooldownSeconds")
    in_cooldown = bool(cooldown and since_fired is not None and since_fired < cooldown)

    fired_now = tripped and not was_tripped and not in_cooldown and not suppressed

    if fired_now:
        ctx.event(resource, "GuardrailTripped",
                  f"conditions met; effect={effect.get('type')}", "Warning")
        last_fired = status.now()
    elif was_tripped and not tripped:
        ctx.event(resource, "GuardrailCleared", "conditions no longer met")

    if suppressed:
        message = f"suppressed for another {int(suppress_for - since_fired)}s after firing"
    elif tripped and in_cooldown:
        message = f"conditions met, in cooldown for another {int(cooldown - since_fired)}s"
    elif tripped:
        message = f"tripped: effect {effect.get('type')}"
    else:
        message = "conditions not met"

    return status.build(
        resource, "active", message,
        [status.condition(status.READY, True, "Evaluated"),
         status.condition(status.TRIGGERED, tripped and not suppressed,
                          "ConditionsMet" if tripped else "ConditionsNotMet")],
        triggered=tripped and not suppressed,
        conditionsMet=tripped,
        suppressed=suppressed,
        inCooldown=in_cooldown,
        effect=effect.get("type"),
        observations=observations,
        lastFiredTime=last_fired,
        lastEvaluationTime=status.now())


# --------------------------------------------------------------------- Tracer
def reconcile_tracer(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Render an OpenTelemetry Collector config from the tracer's resource and
    scope attributes, so agents have somewhere to send traces.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "tracer")

    attributes = {}
    for attribute in spec.get("resource", {}).get("attributes", []):
        value = attribute.get("value", {})
        attributes[attribute["key"]] = value.get("stringValue") or value.get("intValue")

    config = {
        "receivers": {"otlp": {"protocols": {"grpc": {}, "http": {}}}},
        "processors": {
            "resource": {"attributes": [
                {"key": key, "value": value, "action": "upsert"}
                for key, value in attributes.items()
            ]},
            "batch": {},
        },
        "exporters": {"debug": {"verbosity": "basic"}},
        "service": {
            "pipelines": {
                "traces": {"receivers": ["otlp"], "processors": ["resource", "batch"],
                           "exporters": ["debug"]},
                "logs": {"receivers": ["otlp"], "processors": ["resource", "batch"],
                         "exporters": ["debug"]},
            }
        },
    }

    children.ensure_config_map(ctx, children.config_map(
        f"{name}-otel", namespace, labels,
        {"collector.json": json.dumps(config, indent=2)}, owner))

    return status.build(
        resource, "active",
        f"collector config published with {len(attributes)} resource attributes",
        [status.condition(status.READY, True, "ConfigPublished")],
        collectorConfigMap=f"{name}-otel",
        resourceAttributes=sorted(attributes),
        scope=(spec.get("scope") or {}).get("name"))
