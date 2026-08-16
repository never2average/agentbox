"""
Workload Builders
Turns an AgentBox spec into the Kubernetes objects that run it.

Shared by the CRD-less managers in k8s_modules/resources and by the controller,
so the two paths cannot drift apart.
"""
from typing import Dict, Any, List, Optional
from kubernetes import client

DEFAULT_IMAGE = "busybox:latest"
NAME_LABEL = "agentbox.io/resource-name"


def resource_requirements(compute: Dict[str, Any]) -> client.V1ResourceRequirements:
    """
    Build container resources from spec.compute.

    Args:
        compute: The spec.compute block (cpu, gpu, storage)

    Returns:
        Resource requirements with requests and limits
    """
    cpu = compute.get('cpu', {})
    cores = cpu.get('cores', 1)
    memory_mb = cpu.get('memoryMb', 512)

    requests = {'cpu': f"{cores}", 'memory': f"{memory_mb}Mi"}
    limits = {'cpu': f"{cores * 2}", 'memory': f"{memory_mb * 2}Mi"}

    gpu = compute.get('gpu', {})
    if gpu.get('enabled') and gpu.get('count'):
        requests['nvidia.com/gpu'] = str(gpu['count'])
        limits['nvidia.com/gpu'] = str(gpu['count'])

    return client.V1ResourceRequirements(requests=requests, limits=limits)


def probe(health: Dict[str, Any], default_port: Optional[int] = None) -> Optional[client.V1Probe]:
    """
    Build a readiness probe from spec.health.

    Args:
        health: The spec.health block
        default_port: Port to probe when health.port is absent

    Returns:
        Probe, or None if health is not configured usefully
    """
    if not health:
        return None

    port = health.get('port', default_port)
    kind = health.get('type', 'http')

    result = client.V1Probe(
        initial_delay_seconds=health.get('initialDelaySeconds', 10),
        period_seconds=health.get('periodSeconds', 30)
    )

    if kind == 'exec':
        command = health.get('command')
        if not command:
            return None
        result._exec = client.V1ExecAction(command=command)
    elif port is None:
        return None
    elif kind == 'tcp':
        result.tcp_socket = client.V1TCPSocketAction(port=port)
    else:
        result.http_get = client.V1HTTPGetAction(path=health.get('path', '/healthz'), port=port)

    return result


def container(
    name: str,
    spec: Dict[str, Any],
    resource_group: str,
    *,
    ports: Optional[List[int]] = None,
    port_names: Optional[List[str]] = None,
    with_probe: bool = True,
    code_key: str = 'code'
) -> client.V1Container:
    """
    Build the container that runs an AgentBox workload.

    Args:
        name: Container and resource name
        spec: The resource's spec block
        resource_group: Resource group, exposed to the container as an env var
        ports: Container ports to expose
        port_names: Names for those ports, positionally matched
        with_probe: Whether to attach a readiness probe from spec.health
        code_key: Key holding image/entrypoint/args ("code" or "worker")

    Returns:
        Configured container
    """
    code = spec.get(code_key, {})

    result = client.V1Container(
        name=name,
        image=code.get('image', DEFAULT_IMAGE),
        resources=resource_requirements(spec.get('compute') or code.get('compute') or {})
    )

    if code.get('entrypoint'):
        result.command = [code['entrypoint']]
    if code.get('args'):
        result.args = code['args']

    result.env = [
        client.V1EnvVar(name="AGENTBOX_RESOURCE_NAME", value=name),
        client.V1EnvVar(name="AGENTBOX_RESOURCE_GROUP", value=resource_group)
    ]
    for key, value in (spec.get('env') or code.get('env') or {}).items():
        result.env.append(client.V1EnvVar(name=key, value=str(value)))

    if ports:
        names = port_names or [f"port-{p}" for p in ports]
        result.ports = [
            client.V1ContainerPort(name=n[:15], container_port=p)
            for n, p in zip(names, ports)
        ]

    if with_probe:
        readiness = probe(spec.get('health', {}), ports[0] if ports else None)
        if readiness:
            result.readiness_probe = readiness

    return result


def pod_template(name: str, containers: List[client.V1Container],
                 restart_policy: Optional[str] = None) -> client.V1PodTemplateSpec:
    """Build the pod template every AgentBox workload shares."""
    return client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={NAME_LABEL: name}),
        spec=client.V1PodSpec(containers=containers, restart_policy=restart_policy)
    )


def deployment(name: str, namespace: str, labels: Dict[str, str],
               replicas: int, containers: List[client.V1Container],
               owner: Optional[client.V1OwnerReference] = None) -> client.V1Deployment:
    """Build a Deployment for a long-running AgentBox workload."""
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=name, namespace=namespace, labels=labels,
            owner_references=[owner] if owner else None
        ),
        spec=client.V1DeploymentSpec(
            replicas=replicas,
            selector=client.V1LabelSelector(match_labels={NAME_LABEL: name}),
            template=pod_template(name, containers)
        )
    )


def service(name: str, namespace: str, labels: Dict[str, str],
            ports: List[client.V1ServicePort],
            owner: Optional[client.V1OwnerReference] = None) -> client.V1Service:
    """Build a ClusterIP Service in front of an AgentBox workload."""
    return client.V1Service(
        metadata=client.V1ObjectMeta(
            name=name, namespace=namespace, labels=labels,
            owner_references=[owner] if owner else None
        ),
        spec=client.V1ServiceSpec(
            selector={NAME_LABEL: name},
            ports=ports,
            type='ClusterIP'
        )
    )


def job(name: str, namespace: str, labels: Dict[str, str],
        containers: List[client.V1Container], backoff_limit: int = 3,
        owner: Optional[client.V1OwnerReference] = None) -> client.V1Job:
    """Build a Job for a one-shot AgentBox workload."""
    return client.V1Job(
        metadata=client.V1ObjectMeta(
            name=name, namespace=namespace, labels=labels,
            owner_references=[owner] if owner else None
        ),
        spec=client.V1JobSpec(
            template=pod_template(name, containers, restart_policy='Never'),
            backoff_limit=backoff_limit
        )
    )


def cronjob(name: str, namespace: str, labels: Dict[str, str],
            schedule: str, containers: List[client.V1Container],
            backoff_limit: int = 3,
            owner: Optional[client.V1OwnerReference] = None) -> client.V1CronJob:
    """Build a CronJob for a scheduled AgentBox workload."""
    return client.V1CronJob(
        metadata=client.V1ObjectMeta(
            name=name, namespace=namespace, labels=labels,
            owner_references=[owner] if owner else None
        ),
        spec=client.V1CronJobSpec(
            schedule=schedule,
            job_template=client.V1JobTemplateSpec(
                spec=client.V1JobSpec(
                    template=pod_template(name, containers, restart_policy='Never'),
                    backoff_limit=backoff_limit
                )
            ),
            successful_jobs_history_limit=3,
            failed_jobs_history_limit=3
        )
    )


def interval_to_cron(interval_seconds: int) -> str:
    """
    Convert an interval into the nearest cron expression.

    Args:
        interval_seconds: Desired interval

    Returns:
        Cron expression
    """
    minutes = max(1, interval_seconds // 60)
    if minutes >= 1440:
        return f"0 0 */{max(1, minutes // 1440)} * *"
    if minutes >= 60:
        return f"0 */{minutes // 60} * * *"
    return f"*/{minutes} * * * *"


def schedule_expression(schedule: Dict[str, Any]) -> str:
    """
    Read a cron expression out of a schedule block, whatever form it takes.

    Args:
        schedule: A spec.schedule or spec.execution.schedule block

    Returns:
        Cron expression, defaulting to hourly
    """
    if not schedule:
        return '0 * * * *'
    if schedule.get('cronExpression'):
        return schedule['cronExpression']
    if schedule.get('intervalSeconds'):
        return interval_to_cron(schedule['intervalSeconds'])
    return '0 * * * *'
