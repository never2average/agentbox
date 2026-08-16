"""
Status Helpers
Conditions and state, written the way every Kubernetes controller writes them.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

READY = "Ready"
PROGRESSING = "Progressing"
DEGRADED = "Degraded"
VALIDATED = "Validated"
TRIGGERED = "Triggered"


def now() -> str:
    """Current time in RFC 3339, as the API server expects."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def condition(kind: str, status: bool, reason: str, message: str = "") -> Dict[str, Any]:
    """
    Build a status condition.

    Args:
        kind: Condition type, e.g. "Ready"
        status: Whether the condition holds
        reason: Short CamelCase reason
        message: Human-readable detail

    Returns:
        Condition dictionary
    """
    return {
        "type": kind,
        "status": "True" if status else "False",
        "reason": reason,
        "message": message,
        "lastTransitionTime": now(),
    }


def merge_conditions(existing: Optional[List[Dict[str, Any]]],
                     updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge new conditions into the existing set, keeping the original transition
    time when nothing about the condition changed.

    Args:
        existing: Conditions already on the resource
        updates: Conditions this reconcile produced

    Returns:
        Merged condition list
    """
    by_type = {c["type"]: c for c in (existing or [])}
    for update in updates:
        previous = by_type.get(update["type"])
        if previous and previous.get("status") == update["status"] \
                and previous.get("reason") == update["reason"]:
            update["lastTransitionTime"] = previous.get("lastTransitionTime", update["lastTransitionTime"])
        by_type[update["type"]] = update
    return list(by_type.values())


def build(resource: Dict[str, Any], state: str, message: str = "",
          conditions: Optional[List[Dict[str, Any]]] = None,
          **extra: Any) -> Dict[str, Any]:
    """
    Assemble a status object for a resource.

    Args:
        resource: The resource being reconciled
        state: Reported state, e.g. "active"
        message: Human-readable summary
        conditions: Conditions produced by this reconcile
        extra: Kind-specific status fields

    Returns:
        Status dictionary ready to patch
    """
    current = resource.get("status") or {}
    status = {
        "state": state,
        "message": message,
        "observedGeneration": resource["metadata"].get("generation", 0),
        "lastTransitionTime": now(),
        "conditions": merge_conditions(current.get("conditions"), conditions or []),
    }
    status.update(extra)
    return status


def workload_state(replicas: int, ready: int) -> str:
    """Map replica counts onto the shared state vocabulary."""
    if replicas == 0:
        return "suspended"
    if ready == replicas:
        return "active"
    if ready > 0:
        return "degraded"
    return "pending"
