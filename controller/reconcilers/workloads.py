"""
Workload Reconcilers
The kinds that put pods on the cluster: HarnessRuntime, ToolServer, Model,
Gateway, TrainLoop and Evaluator.
"""
import json
import re
from typing import Any, Dict, List

from kubernetes import client

from controller import children, status
from controller.context import Context, logger
from k8s_modules import podspec

RUN_ANNOTATION = "agentbox.io/run"
GATEWAY_KEY_ENV = "AGENTBOX_GATEWAY_API_KEY"
LAST_RUN_ANNOTATION = "agentbox.io/last-run"


def _snake(name: str) -> str:
    """camelCase to snake_case."""
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).lower()


def _litellm_dialect(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Render gateway parameters in LiteLLM's own vocabulary.

    The CRD is camelCase because that is the Kubernetes convention; LiteLLM
    expects snake_case. The controller translates, so the generated config can
    be handed to a real LiteLLM process unchanged.

    Args:
        params: spec.litellmParams, minus credentials

    Returns:
        The same values under LiteLLM's key names
    """
    return {_snake(key): value for key, value in params.items()}


def _enforcement_for(ctx: Context, gateway: str, namespace: str) -> List[Dict[str, Any]]:
    """
    Effects the data plane must apply to this gateway right now.

    Two sources: guardrails that have tripped and scope this gateway, and meters
    whose budget is spent with an onExceed other than alert. The controller
    decides; the gateway enforces.

    Args:
        ctx: Controller context
        gateway: Gateway name
        namespace: Namespace to search

    Returns:
        List of {source, kind, effect, message} entries
    """
    active = []

    for guardrail in ctx.list_resources("guardrails"):
        if guardrail["metadata"]["namespace"] != namespace:
            continue
        state = guardrail.get("status") or {}
        if not state.get("triggered"):
            continue
        scope = guardrail.get("spec", {}).get("scope", {})
        targets = scope.get("targets")
        if targets and gateway not in targets:
            continue
        active.append({
            "source": guardrail["metadata"]["name"],
            "kind": "Guardrail",
            "effect": guardrail["spec"].get("effect", {}).get("type"),
            "parameters": guardrail["spec"].get("effect", {}).get("parameters", {}),
            "message": state.get("message", ""),
        })

    for meter in ctx.list_resources("aimeters"):
        if meter["metadata"]["namespace"] != namespace:
            continue
        state = meter.get("status") or {}
        budget = meter.get("spec", {}).get("budget", {})
        on_exceed = budget.get("onExceed", "alert")
        if not state.get("budgetExceeded") or on_exceed == "alert":
            continue
        subjects = meter.get("spec", {}).get("usage", {}).get("subjects")
        if subjects and gateway not in subjects:
            continue
        active.append({
            "source": meter["metadata"]["name"],
            "kind": "AIMeter",
            "effect": on_exceed,
            "parameters": {},
            "message": f"budget of {budget.get('limit')} exceeded",
        })

    return active


def _service_ports(endpoints: List[Dict[str, Any]]) -> List[client.V1ServicePort]:
    """Turn declared endpoints into Service ports."""
    return [
        client.V1ServicePort(
            name=endpoint["name"][:15],
            port=endpoint["port"],
            target_port=endpoint["port"],
            protocol="TCP"
        )
        for endpoint in endpoints
    ]


def _deployment_status(ctx: Context, name: str, namespace: str) -> Dict[str, Any]:
    """Read replica counts off a Deployment."""
    try:
        deployment = ctx.apps.read_namespaced_deployment(name, namespace)
    except client.exceptions.ApiException:
        return {"replicas": 0, "readyReplicas": 0, "available": 0}
    return {
        "replicas": deployment.spec.replicas or 0,
        "readyReplicas": deployment.status.ready_replicas or 0,
        "available": deployment.status.available_replicas or 0,
    }


def _job_status(ctx: Context, name: str, namespace: str) -> Dict[str, Any]:
    """Read completion counts off a Job."""
    try:
        job = ctx.batch.read_namespaced_job(name, namespace)
    except client.exceptions.ApiException:
        return {}
    return {
        "succeeded": job.status.succeeded or 0,
        "failed": job.status.failed or 0,
        "active": job.status.active or 0,
        "startTime": str(job.status.start_time) if job.status.start_time else None,
        "completionTime": str(job.status.completion_time) if job.status.completion_time else None,
    }


def _job_state(counts: Dict[str, Any]) -> str:
    """Map Job counts onto the shared state vocabulary."""
    if counts.get("succeeded"):
        return "completed"
    if counts.get("failed"):
        return "failed"
    if counts.get("active"):
        return "active"
    return "pending"


# --------------------------------------------------------------- HarnessRuntime
def reconcile_harness_runtime(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a developer's agent image.

    server/worker -> Deployment (+ Service when endpoints are declared)
    batch         -> Job
    cron          -> CronJob
    """
    meta, spec = resource["metadata"], resource.get("spec", {})
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "harness-runtime")
    kind = spec.get("runtimeKind", "server")

    endpoints = spec.get("endpoints", [])
    ports = [e["port"] for e in endpoints]
    names = [e["name"] for e in endpoints]
    volumes, volume_mounts = podspec.mounts(spec.get("files"))
    box = podspec.container(name, spec, "harness-runtime", ports=ports, port_names=names,
                            volume_mounts=volume_mounts)

    if kind in ("server", "worker"):
        children.ensure_deployment(ctx, podspec.deployment(
            name, namespace, labels, spec.get("replicas", 1), [box], owner, volumes))
        if endpoints:
            children.ensure_service(ctx, podspec.service(
                name, namespace, labels, _service_ports(endpoints), owner))

        counts = _deployment_status(ctx, name, namespace)
        state = status.workload_state(counts["replicas"], counts["readyReplicas"])
        return status.build(
            resource, state,
            f"{counts['readyReplicas']}/{counts['replicas']} replicas ready",
            [status.condition(status.READY, state == "active", "DeploymentReady"
                              if state == "active" else "WaitingForPods")],
            replicas=counts["replicas"],
            readyReplicas=counts["readyReplicas"],
            availableReplicas=counts["available"],
            selector=f"{podspec.NAME_LABEL}={name}",
            endpoints=[f"{name}.{namespace}.svc:{e['port']}" for e in endpoints],
        )

    if kind == "cron":
        schedule = podspec.schedule_expression(spec.get("schedule", {}))
        children.ensure_cronjob(ctx, podspec.cronjob(
            name, namespace, labels, schedule, [box], owner=owner, volumes=volumes))
        return status.build(resource, "active", f"scheduled: {schedule}",
                            [status.condition(status.READY, True, "CronJobScheduled")],
                            schedule=schedule)

    children.ensure_job(ctx, podspec.job(name, namespace, labels, [box], owner=owner,
                                        volumes=volumes))
    counts = _job_status(ctx, name, namespace)
    state = _job_state(counts)
    return status.build(resource, state, f"job is {state}",
                        [status.condition(status.READY, state == "completed", "JobFinished"
                                          if state == "completed" else "JobRunning")],
                        **counts)


# ------------------------------------------------------------------- ToolServer
def reconcile_tool_server(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a tool server and publish its tool catalog so callers can discover it.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "tool-server")
    endpoint = spec.get("endpoint", {})
    port = endpoint.get("port")

    if not spec.get("enabled", True):
        children.delete_deployment(ctx, name, namespace)
        children.delete_service(ctx, name, namespace)
        return status.build(resource, "suspended", "spec.enabled is false",
                            [status.condition(status.READY, False, "Disabled")])

    files = dict(spec.get("files") or {})
    files.setdefault("/etc/agentbox", {"name": f"{name}-tools"})
    volumes, volume_mounts = podspec.mounts(files)
    box = podspec.container(name, spec, "tool-server",
                            ports=[port] if port else None, port_names=["tools"],
                            with_probe=False, volume_mounts=volume_mounts)
    children.ensure_deployment(ctx, podspec.deployment(
        name, namespace, labels, spec.get("replicas", 1), [box], owner, volumes))

    if port:
        children.ensure_service(ctx, podspec.service(
            name, namespace, labels,
            [client.V1ServicePort(name=endpoint.get("interface", "http"),
                                  port=port, target_port=port, protocol="TCP")],
            owner))

    base = endpoint.get("basePath", "/")
    catalog = {
        "server": name,
        "address": f"{name}.{namespace}.svc:{port}" if port else None,
        "basePath": base,
        "tools": [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "url": f"http://{name}.{namespace}.svc:{port}"
                       f"{base.rstrip('/')}{tool.get('path', '/' + tool['name'])}",
                "method": tool.get("method", "POST"),
                "parameters": tool.get("parameters", {}),
                "returns": tool.get("returns", {}),
            }
            for tool in spec.get("tools", [])
        ],
    }
    children.ensure_config_map(ctx, children.config_map(
        f"{name}-tools", namespace, labels, {"catalog.json": json.dumps(catalog, indent=2)}, owner))

    counts = _deployment_status(ctx, name, namespace)
    state = status.workload_state(counts["replicas"], counts["readyReplicas"])
    return status.build(
        resource, state,
        f"{len(catalog['tools'])} tools, {counts['readyReplicas']}/{counts['replicas']} ready",
        [status.condition(status.READY, state == "active", "ToolServerReady"
                          if state == "active" else "WaitingForPods")],
        replicas=counts["replicas"],
        readyReplicas=counts["readyReplicas"],
        selector=f"{podspec.NAME_LABEL}={name}",
        tools=[t["name"] for t in catalog["tools"]],
        catalogConfigMap=f"{name}-tools",
        address=catalog["address"],
    )


# ------------------------------------------------------------------------ Model
def reconcile_model(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Serve a model.

    A Model with a serving image gets a Deployment and Service on the GPU node
    groups. A Model without one is a registry entry: the platform records what
    it is and where the weights live, and nothing runs.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    serving = spec.get("serving") or {}

    if not serving.get("image"):
        return status.build(
            resource, "active", "registry entry; no serving image declared",
            [status.condition(status.READY, True, "Registered"),
             status.condition(status.VALIDATED, True, "SpecValid")],
            hubModelId=spec.get("hubModelId"),
        )

    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "model")
    port = serving.get("port", 8000)

    volumes, volume_mounts = podspec.mounts(serving.get("files"))
    box = podspec.container(name, {"code": serving, "compute": serving.get("compute", {}),
                                   "env": serving.get("env", {}),
                                   "health": serving.get("health", {})},
                            "model", ports=[port], port_names=["http"],
                            volume_mounts=volume_mounts,
                            secrets=serving.get("secrets"))
    deployment = podspec.deployment(
        name, namespace, labels, spec.get("replicas", serving.get("replicas", 1)), [box],
        owner, volumes)

    node_selector = serving.get("nodeSelector")
    if node_selector:
        deployment.spec.template.spec.node_selector = node_selector

    children.ensure_deployment(ctx, deployment)
    children.ensure_service(ctx, podspec.service(
        name, namespace, labels,
        [client.V1ServicePort(name="http", port=port, target_port=port, protocol="TCP")],
        owner))

    counts = _deployment_status(ctx, name, namespace)
    state = status.workload_state(counts["replicas"], counts["readyReplicas"])
    return status.build(
        resource, state,
        f"serving {spec.get('modelName')}: {counts['readyReplicas']}/{counts['replicas']} ready",
        [status.condition(status.READY, state == "active", "ModelServing"
                          if state == "active" else "WaitingForPods")],
        replicas=counts["replicas"],
        readyReplicas=counts["readyReplicas"],
        selector=f"{podspec.NAME_LABEL}={name}",
        address=f"{name}.{namespace}.svc:{port}",
    )


# ---------------------------------------------------------------------- Gateway
def reconcile_gateway(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Render the gateway's routing config and, when an image is declared, run it.

    The config is a LiteLLM model list built from the spec, so the same object
    describes routing whether you run the gateway here or point an existing one
    at the generated ConfigMap.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "gateway")

    declared = spec.get("litellmParams", {})
    params = {k: v for k, v in declared.items()
              if k not in ("apiKey", "apiKeySecretRef")}

    # The credential is referenced, not copied. LiteLLM reads os.environ/NAME,
    # and the controller injects NAME into the pod from the Secret.
    api_key_ref = declared.get("apiKeySecretRef")
    gateway_secrets = {}
    if api_key_ref:
        params["apiKey"] = f"os.environ/{GATEWAY_KEY_ENV}"
        gateway_secrets[GATEWAY_KEY_ENV] = api_key_ref

    enforcement = _enforcement_for(ctx, name, namespace)
    config = {
        "model_list": [{
            "model_name": spec["modelName"],
            "litellm_params": _litellm_dialect(params),
            "model_info": _litellm_dialect(spec.get("modelInfo", {})),
        }],
        # What the data plane must act on right now. A gateway reads this and
        # throttles, blocks or reroutes; the controller only decides.
        "enforcement": enforcement,
    }
    children.ensure_config_map(ctx, children.config_map(
        f"{name}-config", namespace, labels,
        {"config.json": json.dumps(config, indent=2)}, owner))

    serving = spec.get("serving") or {}
    if not serving.get("image"):
        return status.build(
            resource, "active",
            f"routing config published for {spec['modelName']}",
            [status.condition(status.READY, True, "ConfigPublished")],
            configMap=f"{name}-config",
            upstream=params.get("model"),
            credentialFrom=api_key_ref["name"] if api_key_ref else None,
            enforcing=[e["source"] for e in enforcement],
        )

    port = serving.get("port", 4000)
    files = dict(serving.get("files") or {})
    files.setdefault("/etc/agentbox", {"name": f"{name}-config"})
    volumes, volume_mounts = podspec.mounts(files)
    secrets = dict(serving.get("secrets") or {})
    secrets.update(gateway_secrets)
    box = podspec.container(name, {"code": serving, "compute": serving.get("compute", {}),
                                   "env": serving.get("env", {}),
                                   "health": serving.get("health", {})},
                            "gateway", ports=[port], port_names=["http"],
                            volume_mounts=volume_mounts, secrets=secrets)
    children.ensure_deployment(ctx, podspec.deployment(
        name, namespace, labels, serving.get("replicas", 1), [box], owner, volumes))
    children.ensure_service(ctx, podspec.service(
        name, namespace, labels,
        [client.V1ServicePort(name="http", port=port, target_port=port, protocol="TCP")],
        owner))

    counts = _deployment_status(ctx, name, namespace)
    state = status.workload_state(counts["replicas"], counts["readyReplicas"])
    return status.build(
        resource, state, f"gateway for {spec['modelName']}",
        [status.condition(status.READY, state == "active", "GatewayReady"
                          if state == "active" else "WaitingForPods")],
        configMap=f"{name}-config",
        upstream=params.get("model"),
        credentialFrom=api_key_ref["name"] if api_key_ref else None,
        address=f"{name}.{namespace}.svc:{port}",
        replicas=counts["replicas"],
        readyReplicas=counts["readyReplicas"],
        enforcing=[e["source"] for e in enforcement],
    )


# -------------------------------------------------------------------- TrainLoop
def reconcile_train_loop(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a training loop: a CronJob when it is scheduled, a Job otherwise.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "train-loop")

    if spec.get("status") in ("paused", "stopped"):
        return status.build(resource, "suspended", f"spec.status is {spec['status']}",
                            [status.condition(status.READY, False, "Suspended")])

    execution = spec.get("execution", {})
    restarts = spec.get("lifecycle", {}).get("restartPolicy", {}).get("maxRestarts", 3)
    box = podspec.container(name, spec, "train-loop", with_probe=False, code_key="worker")

    schedule = execution.get("schedule")
    if schedule:
        expression = podspec.schedule_expression(schedule)
        children.ensure_cronjob(ctx, podspec.cronjob(
            name, namespace, labels, expression, [box], restarts, owner))
        return status.build(resource, "active", f"scheduled: {expression}",
                            [status.condition(status.READY, True, "CronJobScheduled")],
                            schedule=expression)

    children.ensure_job(ctx, podspec.job(name, namespace, labels, [box], restarts, owner))
    counts = _job_status(ctx, name, namespace)
    state = _job_state(counts)
    return status.build(resource, state, f"training job is {state}",
                        [status.condition(status.READY, state == "completed", "JobFinished"
                                          if state == "completed" else "JobRunning")],
                        **counts)


# -------------------------------------------------------------------- Evaluator
def reconcile_evaluator(ctx: Context, resource: Dict[str, Any]) -> Dict[str, Any]:
    """
    Publish the evaluation suite, and run it when asked.

    Annotating the object with `agentbox.io/run: <id>` starts a Job; the id is
    recorded so the same request is not run twice.
    """
    meta, spec = resource["metadata"], resource["spec"]
    name, namespace = meta["name"], meta["namespace"]
    owner = children.owner_reference(resource)
    labels = children.labels_for(resource, "evaluator")
    annotations = meta.get("annotations", {})

    dataset = spec.get("dataset", {})
    source = next((k for k in ("inline", "file", "ioConnector") if k in dataset), None)
    cases = len(dataset.get("inline", {}).get("cases", [])) if source == "inline" else None
    metrics = [m["metric"]["type"] for m in spec.get("scoring", {}).get("metrics", [])]

    children.ensure_config_map(ctx, children.config_map(
        f"{name}-suite", namespace, labels,
        {"suite.json": json.dumps({"dataset": dataset, "scoring": spec.get("scoring", {}),
                                   "runConfig": spec.get("runConfig", {})}, indent=2)},
        owner))

    requested = annotations.get(RUN_ANNOTATION)
    last_run = (resource.get("status") or {}).get("lastRunId")
    extra = {"suiteConfigMap": f"{name}-suite", "datasetSource": source,
             "caseCount": cases, "metrics": metrics}

    if requested and requested != last_run:
        runner = spec.get("runConfig", {}).get("image")
        if not runner:
            ctx.event(resource, "RunSkipped",
                      "spec.runConfig.image is required to run this suite", "Warning")
            return status.build(resource, "failed",
                                "a run was requested but spec.runConfig.image is not set",
                                [status.condition(status.READY, False, "NoRunnerImage")],
                                lastRunId=requested, **extra)

        job_name = f"{name}-{requested}"[:63]
        box = podspec.container(job_name, {"code": {"image": runner},
                                           "env": {"AGENTBOX_SUITE": f"{name}-suite"}},
                                "evaluator", with_probe=False)
        children.ensure_job(ctx, podspec.job(job_name, namespace, labels, [box], owner=owner))
        ctx.event(resource, "RunStarted", f"evaluation run {requested} started")
        return status.build(resource, "active", f"run {requested} started",
                            [status.condition(status.READY, True, "RunStarted")],
                            lastRunId=requested, lastRunJob=job_name, **extra)

    return status.build(
        resource, "active",
        f"{cases if cases is not None else 'external'} cases, {len(metrics)} metrics",
        [status.condition(status.READY, True, "SuitePublished"),
         status.condition(status.VALIDATED, bool(source), "DatasetResolved"
                          if source else "NoDatasetSource")],
        lastRunId=last_run, **extra)
