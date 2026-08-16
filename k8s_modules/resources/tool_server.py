"""
ToolServer Resource Manager
Manages ToolServer CRDs backed by Kubernetes Deployments and Services
"""
from typing import Dict, Any
from kubernetes import client
from k8s_modules.base_resource import BaseResourceManager
from k8s_modules import resource_store


class ToolServerManager(BaseResourceManager):
    """
    Manager for ToolServer resources.

    A tool server runs an image that serves its tools over HTTP or gRPC:
    - Deployment for the server itself
    - Service exposing spec.endpoint
    """

    @property
    def resource_group(self) -> str:
        """Return the resource group name."""
        return "tool-server"

    def _create_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Create Deployment and Service for the tool server.

        Args:
            name: Tool server name
            spec: Tool server specification
        """
        self._create_deployment(name, spec)
        self._create_service(name, spec)

    def _update_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Update Deployment and Service for the tool server.

        Args:
            name: Tool server name
            spec: Updated specification
        """
        self._update_deployment(name, spec)
        self._delete_service(name)
        self._create_service(name, spec)

    def _delete_workloads(self, name: str) -> None:
        """
        Delete Deployment and Service for the tool server.

        Args:
            name: Tool server name
        """
        self._delete_deployment(name)
        self._delete_service(name)

    def _synthesize_status(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesize status from the backing Deployment.

        Args:
            name: Tool server name
            spec: Specification

        Returns:
            Status dictionary
        """
        name = resource_store.sanitize_name(name)

        try:
            deployment = self.apps_v1_api.read_namespaced_deployment(
                name=name,
                namespace=self.namespace
            )
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise
            if 'status' in spec:
                return spec['status']
            return {
                'state': 'inactive',
                'message': 'Workload not found'
            }

        replicas = deployment.spec.replicas or 0
        ready_replicas = deployment.status.ready_replicas or 0

        if ready_replicas == replicas and replicas > 0:
            state = 'active'
        elif ready_replicas > 0:
            state = 'degraded'
        else:
            state = 'inactive'

        return {
            'state': state,
            'replicas': replicas,
            'readyReplicas': ready_replicas,
            'availableReplicas': deployment.status.available_replicas or 0,
            'selector': f"agentbox.io/resource-name={name}",
            'tools': [tool['name'] for tool in spec.get('spec', {}).get('tools', [])]
        }

    def _build_container(self, name: str, spec: Dict[str, Any]) -> client.V1Container:
        """Build the tool server container from spec.code, spec.compute and spec.env."""
        server = spec.get('spec', {})

        code = server.get('code', {})
        cpu_spec = server.get('compute', {}).get('cpu', {})
        memory_mb = cpu_spec.get('memoryMb', 512)
        cpu_cores = cpu_spec.get('cores', 1)

        container = client.V1Container(
            name=name,
            image=code.get('image', 'busybox:latest'),
            resources=client.V1ResourceRequirements(
                requests={
                    'cpu': f"{cpu_cores}",
                    'memory': f"{memory_mb}Mi"
                },
                limits={
                    'cpu': f"{cpu_cores * 2}",
                    'memory': f"{memory_mb * 2}Mi"
                }
            )
        )

        if code.get('entrypoint'):
            container.command = [code['entrypoint']]
        if code.get('args'):
            container.args = code['args']

        container.env = [
            client.V1EnvVar(name="AGENTBOX_RESOURCE_NAME", value=name),
            client.V1EnvVar(name="AGENTBOX_RESOURCE_GROUP", value=self.resource_group)
        ]
        for key, value in server.get('env', {}).items():
            container.env.append(client.V1EnvVar(name=key, value=str(value)))

        port = server.get('endpoint', {}).get('port')
        if port:
            container.ports = [client.V1ContainerPort(name='tools', container_port=port)]

        return container

    def _create_deployment(self, name: str, spec: Dict[str, Any]) -> None:
        """Create Kubernetes Deployment for the tool server."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)

        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels=labels
            ),
            spec=client.V1DeploymentSpec(
                replicas=spec.get('spec', {}).get('replicas', 1),
                selector=client.V1LabelSelector(
                    match_labels={"agentbox.io/resource-name": name}
                ),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={"agentbox.io/resource-name": name}
                    ),
                    spec=client.V1PodSpec(
                        containers=[self._build_container(name, spec)]
                    )
                )
            )
        )

        try:
            self.apps_v1_api.create_namespaced_deployment(
                namespace=self.namespace,
                body=deployment
            )
        except client.exceptions.ApiException as e:
            if e.status != 409:  # Already exists
                raise

    def _update_deployment(self, name: str, spec: Dict[str, Any]) -> None:
        """Update Kubernetes Deployment for the tool server."""
        name = resource_store.sanitize_name(name)

        try:
            deployment = self.apps_v1_api.read_namespaced_deployment(
                name=name,
                namespace=self.namespace
            )

            deployment.spec.template.spec.containers = [self._build_container(name, spec)]
            deployment.spec.replicas = spec.get('spec', {}).get('replicas', deployment.spec.replicas)

            self.apps_v1_api.patch_namespaced_deployment(
                name=name,
                namespace=self.namespace,
                body=deployment
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self._create_deployment(name, spec)

    def _delete_deployment(self, name: str) -> None:
        """Delete Kubernetes Deployment."""
        name = resource_store.sanitize_name(name)
        try:
            self.apps_v1_api.delete_namespaced_deployment(
                name=name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(propagation_policy='Foreground')
            )
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    def _create_service(self, name: str, spec: Dict[str, Any]) -> None:
        """Create Kubernetes Service exposing the tool server endpoint."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)

        endpoint = spec.get('spec', {}).get('endpoint', {})
        port = endpoint.get('port')
        if not port:
            return

        service = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels=labels
            ),
            spec=client.V1ServiceSpec(
                selector={"agentbox.io/resource-name": name},
                ports=[
                    client.V1ServicePort(
                        name=endpoint.get('interface', 'http'),
                        port=port,
                        target_port=port,
                        protocol='TCP'
                    )
                ],
                type='ClusterIP'
            )
        )

        try:
            self.core_v1_api.create_namespaced_service(
                namespace=self.namespace,
                body=service
            )
        except client.exceptions.ApiException as e:
            if e.status != 409:
                raise

    def _delete_service(self, name: str) -> None:
        """Delete Kubernetes Service."""
        name = resource_store.sanitize_name(name)
        try:
            self.core_v1_api.delete_namespaced_service(
                name=name,
                namespace=self.namespace
            )
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise
