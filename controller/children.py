"""
Child Resources
Create-or-update helpers for the Kubernetes objects AgentBox resources own.

Every child carries an owner reference back to its AgentBox resource, so the
garbage collector removes it when the parent goes away — no finalizers needed.
"""
from typing import Any, Callable, Dict, List, Optional

from kubernetes import client

from controller.context import API_VERSION, FIELD_MANAGER, Context, logger

MANAGED_BY = "agentbox.io/managed-by"
GROUP_LABEL = "agentbox.io/resource-group"
NAME_LABEL = "agentbox.io/resource-name"


def owner_reference(resource: Dict[str, Any]) -> client.V1OwnerReference:
    """
    Build the owner reference that ties a child to its AgentBox resource.

    Args:
        resource: The owning resource

    Returns:
        Owner reference with controller semantics
    """
    meta = resource["metadata"]
    return client.V1OwnerReference(
        api_version=API_VERSION,
        kind=resource["kind"],
        name=meta["name"],
        uid=meta["uid"],
        controller=True,
        block_owner_deletion=True
    )


def labels_for(resource: Dict[str, Any], group: str) -> Dict[str, str]:
    """Standard labels every child carries."""
    return {
        "app.kubernetes.io/part-of": "agentbox",
        "app.kubernetes.io/name": resource["metadata"]["name"],
        MANAGED_BY: FIELD_MANAGER,
        GROUP_LABEL: group,
        NAME_LABEL: resource["metadata"]["name"],
    }


def _ensure(read: Callable, create: Callable, replace: Callable,
            name: str, namespace: str, body: Any, mutate: Callable) -> str:
    """
    Create a child, or update it in place when it already exists.

    Args:
        read: Function reading the existing object
        create: Function creating a new object
        replace: Function replacing an existing object
        name: Object name
        namespace: Object namespace
        body: Desired object
        mutate: Applies the desired state onto the existing object

    Returns:
        "created", "updated" or "unchanged"
    """
    try:
        existing = read(name, namespace)
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise
        create(namespace, body)
        return "created"

    before = client.ApiClient().sanitize_for_serialization(existing)
    mutate(existing, body)
    after = client.ApiClient().sanitize_for_serialization(existing)
    if before == after:
        return "unchanged"

    replace(name, namespace, existing)
    return "updated"


def ensure_deployment(ctx: Context, body: client.V1Deployment) -> str:
    """Create or update a Deployment."""
    def mutate(existing, desired):
        existing.spec.template = desired.spec.template
        existing.spec.replicas = desired.spec.replicas
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.apps.read_namespaced_deployment(n, ns),
        lambda ns, b: ctx.apps.create_namespaced_deployment(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.apps.replace_namespaced_deployment(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_service(ctx: Context, body: client.V1Service) -> str:
    """Create or update a Service, preserving its cluster IP."""
    def mutate(existing, desired):
        existing.spec.ports = desired.spec.ports
        existing.spec.selector = desired.spec.selector
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.core.read_namespaced_service(n, ns),
        lambda ns, b: ctx.core.create_namespaced_service(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.core.replace_namespaced_service(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_config_map(ctx: Context, body: client.V1ConfigMap) -> str:
    """Create or update a ConfigMap."""
    def mutate(existing, desired):
        existing.data = desired.data
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.core.read_namespaced_config_map(n, ns),
        lambda ns, b: ctx.core.create_namespaced_config_map(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.core.replace_namespaced_config_map(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_secret(ctx: Context, body: client.V1Secret) -> str:
    """Create or update a Secret."""
    def mutate(existing, desired):
        existing.string_data = desired.string_data
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.core.read_namespaced_secret(n, ns),
        lambda ns, b: ctx.core.create_namespaced_secret(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.core.replace_namespaced_secret(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_service_account(ctx: Context, body: client.V1ServiceAccount) -> str:
    """Create or update a ServiceAccount."""
    def mutate(existing, desired):
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.annotations = desired.metadata.annotations
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.core.read_namespaced_service_account(n, ns),
        lambda ns, b: ctx.core.create_namespaced_service_account(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.core.replace_namespaced_service_account(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_role(ctx: Context, body: client.V1Role) -> str:
    """Create or update a Role."""
    def mutate(existing, desired):
        existing.rules = desired.rules
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.rbac.read_namespaced_role(n, ns),
        lambda ns, b: ctx.rbac.create_namespaced_role(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.rbac.replace_namespaced_role(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_role_binding(ctx: Context, body: client.V1RoleBinding) -> str:
    """Create or update a RoleBinding."""
    def mutate(existing, desired):
        existing.subjects = desired.subjects
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.rbac.read_namespaced_role_binding(n, ns),
        lambda ns, b: ctx.rbac.create_namespaced_role_binding(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.rbac.replace_namespaced_role_binding(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_cronjob(ctx: Context, body: client.V1CronJob) -> str:
    """Create or update a CronJob."""
    def mutate(existing, desired):
        existing.spec.schedule = desired.spec.schedule
        existing.spec.job_template = desired.spec.job_template
        existing.metadata.labels = desired.metadata.labels
        existing.metadata.owner_references = desired.metadata.owner_references

    return _ensure(
        lambda n, ns: ctx.batch.read_namespaced_cron_job(n, ns),
        lambda ns, b: ctx.batch.create_namespaced_cron_job(ns, b, field_manager=FIELD_MANAGER),
        lambda n, ns, b: ctx.batch.replace_namespaced_cron_job(n, ns, b, field_manager=FIELD_MANAGER),
        body.metadata.name, body.metadata.namespace, body, mutate)


def ensure_job(ctx: Context, body: client.V1Job) -> str:
    """
    Create a Job if it is absent. Jobs are immutable, so existing ones are left alone.

    Args:
        ctx: Controller context
        body: Desired Job

    Returns:
        "created" or "unchanged"
    """
    try:
        ctx.batch.read_namespaced_job(body.metadata.name, body.metadata.namespace)
        return "unchanged"
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise
    ctx.batch.create_namespaced_job(body.metadata.namespace, body, field_manager=FIELD_MANAGER)
    return "created"


def delete_deployment(ctx: Context, name: str, namespace: str) -> None:
    """Delete a Deployment, ignoring absence."""
    try:
        ctx.apps.delete_namespaced_deployment(
            name, namespace, body=client.V1DeleteOptions(propagation_policy='Foreground'))
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise


def delete_service(ctx: Context, name: str, namespace: str) -> None:
    """Delete a Service, ignoring absence."""
    try:
        ctx.core.delete_namespaced_service(name, namespace)
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise


def config_map(name: str, namespace: str, labels: Dict[str, str],
               data: Dict[str, str], owner: client.V1OwnerReference) -> client.V1ConfigMap:
    """Build an owned ConfigMap."""
    return client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels,
                                     owner_references=[owner]),
        data=data
    )
