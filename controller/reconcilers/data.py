"""
Data Reconcilers
Dataset and Recipe: the kinds that describe where data comes from and what
order work happens in.
"""
import json
from typing import Any, Dict, List, Set

from controller import children, status
from controller.context import Context

CONNECTOR_ADDRESS = {
    "httpPoll": lambda c: c.get("url"),
    "webhook": lambda c: c.get("path"),
    "kafka": lambda c: ",".join(c.get("brokers") or []) or None,
    "queue": lambda c: c.get("queueUrl") or c.get("queueName"),
    "s3": lambda c: f"s3://{c.get('bucket', '')}/{c.get('prefix', '')}".rstrip("/"),
    "fs": lambda c: c.get("basePath"),
    "database": lambda c: (c.get("connection") or {}).get("host"),
    "grpc": lambda c: c.get("target") or c.get("endpoint"),
    "pubsub": lambda c: (c.get("subscription") or {}).get("id") or c.get("topic"),
}


# -------------------------------------------------------------------- Dataset
def reconcile_dataset(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish a dataset's connector configuration so workloads can mount it.

    The controller does not move data; it makes the binding concrete and
    reports whether the declared variant matches the declared type.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "dataset")

    kind = spec["type"]
    config = spec.get("config", {}).get(kind, {})

    if not spec.get("enabled", True):
        return status.build(resource, "suspended", "spec.enabled is false",
                            [status.condition(status.READY, False, "Disabled")])

    address = CONNECTOR_ADDRESS.get(kind, lambda c: None)(config)
    published = {
        "name": name,
        "type": kind,
        "direction": spec["direction"],
        "address": address,
        "config": config,
        "stateManagement": spec.get("stateManagement", {}),
    }

    children.ensure_config_map(ctx, children.config_map(
        f"{name}-connector", namespace, labels,
        {"connector.json": json.dumps(published, indent=2)}, owner))

    state_config = spec.get("stateManagement", {})
    checkpointing = bool(state_config.get("enabled"))

    return status.build(
        resource, "active",
        f"{kind} {spec['direction']}" + (f" at {address}" if address else ""),
        [status.condition(status.READY, True, "ConnectorPublished"),
         status.condition(status.VALIDATED, bool(config), "ConfigPresent"
                          if config else "ConfigEmpty")],
        connectorConfigMap=f"{name}-connector",
        address=address,
        checkpointing=checkpointing)


# --------------------------------------------------------------------- Recipe
def _resolve_order(stages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Topologically sort a recipe's stages.

    Args:
        stages: The executionDefinition.stages list

    Returns:
        {"order": [...], "error": str or None}
    """
    by_id = {stage["id"]: stage for stage in stages}
    unknown = {
        dependency
        for stage in stages
        for dependency in stage.get("dependsOn", [])
        if dependency not in by_id
    }
    if unknown:
        return {"order": [], "error": f"unknown dependencies: {sorted(unknown)}"}

    order: List[str] = []
    permanent: Set[str] = set()
    temporary: Set[str] = set()
    cycle: List[str] = []

    def visit(stage_id: str) -> bool:
        if stage_id in permanent:
            return True
        if stage_id in temporary:
            cycle.append(stage_id)
            return False
        temporary.add(stage_id)
        for dependency in by_id[stage_id].get("dependsOn", []):
            if not visit(dependency):
                return False
        temporary.discard(stage_id)
        permanent.add(stage_id)
        order.append(stage_id)
        return True

    for stage in stages:
        if not visit(stage["id"]):
            return {"order": [], "error": f"dependency cycle at {cycle[0]}"}

    return {"order": order, "error": None}


def reconcile_recipe(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate a recipe's stage graph and publish the resolved execution order.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "recipe")

    core = spec.get("coreMetadata", {})
    stages = spec.get("executionDefinition", {}).get("stages", [])

    duplicates = sorted({s["id"] for s in stages if [x["id"] for x in stages].count(s["id"]) > 1})
    if duplicates:
        return status.build(
            resource, "failed", f"duplicate stage ids: {duplicates}",
            [status.condition(status.READY, False, "DuplicateStageIds"),
             status.condition(status.VALIDATED, False, "DuplicateStageIds")])

    resolved = _resolve_order(stages)
    if resolved["error"]:
        ctx.event(resource, "InvalidRecipe", resolved["error"], "Warning")
        return status.build(
            resource, "failed", resolved["error"],
            [status.condition(status.READY, False, "InvalidStageGraph"),
             status.condition(status.VALIDATED, False, "InvalidStageGraph")])

    if core.get("status") in ("inactive", "deprecated"):
        return status.build(
            resource, "inactive", f"coreMetadata.status is {core['status']}",
            [status.condition(status.READY, False, "NotActive"),
             status.condition(status.VALIDATED, True, "StageGraphValid")],
            resolvedOrder=resolved["order"])

    children.ensure_config_map(ctx, children.config_map(
        f"{name}-plan", namespace, labels,
        {"plan.json": json.dumps({"type": core.get("type"),
                                  "order": resolved["order"],
                                  "stages": stages}, indent=2)}, owner))

    return status.build(
        resource, "active",
        f"{len(stages)} stages resolved: {' -> '.join(resolved['order'])}",
        [status.condition(status.READY, True, "PlanPublished"),
         status.condition(status.VALIDATED, True, "StageGraphValid")],
        planConfigMap=f"{name}-plan",
        stageCount=len(stages),
        resolvedOrder=resolved["order"])
