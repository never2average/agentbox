"""
HarnessRuntime Resource Manager
Manages HarnessRuntime CRDs backed by Kubernetes Deployments, Services, Jobs, and CronJobs
"""
from typing import Dict, Any, Optional
from kubernetes import client
from k8s_modules.base_resource import BaseResourceManager
from k8s_modules import resource_store


class HarnessRuntimeManager(BaseResourceManager):
    """
    Manager for HarnessRuntime resources.
    
    Maps spec.runtimeKind to Kubernetes workloads:
    - server/worker -> Deployment (+ Service if endpoints present)
    - batch -> Job
    - cron -> CronJob
    """
    
    @property
    def resource_group(self) -> str:
        """Return the resource group name."""
        return "harness-runtime"
    
    def _create_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Create Kubernetes workloads for runtime.
        
        Args:
            name: Runtime name
            spec: Runtime specification
        """
        runtime_kind = spec.get('spec', {}).get('runtimeKind', 'server')
        
        if runtime_kind in ['server', 'worker']:
            self._create_deployment(name, spec)
            # Create service if endpoints are defined
            if spec.get('spec', {}).get('endpoints'):
                self._create_service(name, spec)
        elif runtime_kind == 'batch':
            self._create_job(name, spec)
        elif runtime_kind == 'cron':
            self._create_cronjob(name, spec)
    
    def _update_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Update Kubernetes workloads for runtime.
        
        Args:
            name: Runtime name
            spec: Updated specification
        """
        runtime_kind = spec.get('spec', {}).get('runtimeKind', 'server')
        
        if runtime_kind in ['server', 'worker']:
            self._update_deployment(name, spec)
            # Update or create/delete service based on endpoints
            if spec.get('spec', {}).get('endpoints'):
                self._update_service(name, spec)
            else:
                self._delete_service(name)
        elif runtime_kind == 'batch':
            # Jobs are immutable, need to recreate
            self._delete_job(name)
            self._create_job(name, spec)
        elif runtime_kind == 'cron':
            self._update_cronjob(name, spec)
    
    def _delete_workloads(self, name: str) -> None:
        """
        Delete Kubernetes workloads for runtime.
        
        Args:
            name: Runtime name
        """
        # Try to delete all possible workload types
        self._delete_deployment(name)
        self._delete_service(name)
        self._delete_job(name)
        self._delete_cronjob(name)
    
    def _synthesize_status(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesize status from Kubernetes workloads.
        
        Args:
            name: Runtime name
            spec: Specification
            
        Returns:
            Status dictionary
        """
        runtime_kind = spec.get('spec', {}).get('runtimeKind', 'server')
        name = resource_store.sanitize_name(name)
        
        status = {
            'state': 'unknown',
            'message': 'No workload found'
        }
        
        try:
            if runtime_kind in ['server', 'worker']:
                deployment = self.apps_v1_api.read_namespaced_deployment(
                    name=name,
                    namespace=self.namespace
                )
                
                replicas = deployment.spec.replicas or 0
                ready_replicas = deployment.status.ready_replicas or 0
                available_replicas = deployment.status.available_replicas or 0
                
                if ready_replicas == replicas and replicas > 0:
                    state = 'active'
                elif ready_replicas > 0:
                    state = 'degraded'
                else:
                    state = 'inactive'
                
                status = {
                    'state': state,
                    'replicas': replicas,
                    'readyReplicas': ready_replicas,
                    'availableReplicas': available_replicas,
                    'selector': f"agentbox.io/resource-name={name}",
                    'conditions': [
                        {
                            'type': c.type,
                            'status': c.status,
                            'reason': c.reason,
                            'message': c.message
                        } for c in (deployment.status.conditions or [])
                    ]
                }
            
            elif runtime_kind == 'batch':
                job = self.batch_v1_api.read_namespaced_job(
                    name=name,
                    namespace=self.namespace
                )
                
                succeeded = job.status.succeeded or 0
                failed = job.status.failed or 0
                active = job.status.active or 0
                
                if succeeded > 0:
                    state = 'completed'
                elif failed > 0:
                    state = 'failed'
                elif active > 0:
                    state = 'active'
                else:
                    state = 'pending'
                
                status = {
                    'state': state,
                    'succeeded': succeeded,
                    'failed': failed,
                    'active': active,
                    'startTime': str(job.status.start_time) if job.status.start_time else None,
                    'completionTime': str(job.status.completion_time) if job.status.completion_time else None
                }
            
            elif runtime_kind == 'cron':
                cronjob = self.batch_v1_api.read_namespaced_cron_job(
                    name=name,
                    namespace=self.namespace
                )
                
                active_jobs = len(cronjob.status.active or [])
                last_schedule_time = cronjob.status.last_schedule_time
                last_successful_time = cronjob.status.last_successful_time
                
                status = {
                    'state': 'active' if cronjob.spec.suspend is False else 'suspended',
                    'activeJobs': active_jobs,
                    'lastScheduleTime': str(last_schedule_time) if last_schedule_time else None,
                    'lastSuccessfulTime': str(last_successful_time) if last_successful_time else None,
                    'schedule': cronjob.spec.schedule
                }
        
        except client.exceptions.ApiException as e:
            if e.status == 404:
                # Return stored status if available
                if 'status' in spec:
                    return spec['status']
                status = {
                    'state': 'inactive',
                    'message': 'Workload not found'
                }
        
        return status
    
    def _build_container(self, name: str, spec: Dict[str, Any]) -> client.V1Container:
        """Build the harness container from spec.code, spec.compute and spec.env."""
        harness = spec.get('spec', {})
        
        code = harness.get('code', {})
        image = code.get('image', 'busybox:latest')
        entrypoint = code.get('entrypoint')
        args = code.get('args', [])
        
        cpu_spec = harness.get('compute', {}).get('cpu', {})
        memory_mb = cpu_spec.get('memoryMb', 512)
        cpu_cores = cpu_spec.get('cores', 1)
        
        container = client.V1Container(
            name=name,
            image=image,
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
        
        if entrypoint:
            container.command = [entrypoint]
        if args:
            container.args = args
        
        container.env = [
            client.V1EnvVar(name="AGENTBOX_RESOURCE_NAME", value=name),
            client.V1EnvVar(name="AGENTBOX_RESOURCE_GROUP", value=self.resource_group)
        ]
        for key, value in harness.get('env', {}).items():
            container.env.append(client.V1EnvVar(name=key, value=str(value)))
        
        ports = [
            client.V1ContainerPort(name=e['name'][:15], container_port=e['port'])
            for e in harness.get('endpoints', [])
        ]
        if ports:
            container.ports = ports
        
        probe = self._build_probe(harness.get('health', {}), harness.get('endpoints', []))
        if probe:
            container.readiness_probe = probe
        
        return container
    
    def _build_probe(self, health: Dict[str, Any], endpoints: list) -> Optional[client.V1Probe]:
        """Build a readiness probe from spec.health."""
        if not health:
            return None
        
        default_port = endpoints[0]['port'] if endpoints else None
        port = health.get('port', default_port)
        probe_type = health.get('type', 'http')
        
        probe = client.V1Probe(
            initial_delay_seconds=health.get('initialDelaySeconds', 10),
            period_seconds=health.get('periodSeconds', 30)
        )
        
        if probe_type == 'exec':
            command = health.get('command')
            if not command:
                return None
            probe._exec = client.V1ExecAction(command=command)
        elif port is None:
            return None
        elif probe_type == 'tcp':
            probe.tcp_socket = client.V1TCPSocketAction(port=port)
        else:
            probe.http_get = client.V1HTTPGetAction(
                path=health.get('path', '/healthz'),
                port=port
            )
        
        return probe
    
    def _create_deployment(self, name: str, spec: Dict[str, Any]) -> None:
        """Create Kubernetes Deployment for the harness."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        container = self._build_container(name, spec)
        
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
                        containers=[container]
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
        """Update Kubernetes Deployment for the harness."""
        name = resource_store.sanitize_name(name)
        
        try:
            # Read existing deployment
            deployment = self.apps_v1_api.read_namespaced_deployment(
                name=name,
                namespace=self.namespace
            )
            
            # Replace the container and replica count from spec
            deployment.spec.template.spec.containers = [self._build_container(name, spec)]
            deployment.spec.replicas = spec.get('spec', {}).get('replicas', deployment.spec.replicas)
            
            # Update deployment
            self.apps_v1_api.patch_namespaced_deployment(
                name=name,
                namespace=self.namespace,
                body=deployment
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                # Create if doesn't exist
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
        """Create Kubernetes Service for runtime with endpoints."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        endpoints = spec.get('spec', {}).get('endpoints', [])
        if not endpoints:
            return
        
        ports = [
            client.V1ServicePort(
                name=endpoint['name'][:15],
                port=endpoint['port'],
                target_port=endpoint['port'],
                protocol='TCP'
            )
            for endpoint in endpoints
        ]
        
        if not ports:
            return
        
        service = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels=labels
            ),
            spec=client.V1ServiceSpec(
                selector={"agentbox.io/resource-name": name},
                ports=ports,
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
    
    def _update_service(self, name: str, spec: Dict[str, Any]) -> None:
        """Update or create Kubernetes Service."""
        name = resource_store.sanitize_name(name)
        
        try:
            # Check if exists
            self.core_v1_api.read_namespaced_service(
                name=name,
                namespace=self.namespace
            )
            # Service exists, delete and recreate (easier than patching)
            self._delete_service(name)
            self._create_service(name, spec)
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self._create_service(name, spec)
    
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
    
    def _create_job(self, name: str, spec: Dict[str, Any]) -> None:
        """Create Kubernetes Job for a batch harness."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        container = self._build_container(name, spec)
        
        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels=labels
            ),
            spec=client.V1JobSpec(
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={"agentbox.io/resource-name": name}
                    ),
                    spec=client.V1PodSpec(
                        containers=[container],
                        restart_policy='Never'
                    )
                ),
                backoff_limit=3
            )
        )
        
        try:
            self.batch_v1_api.create_namespaced_job(
                namespace=self.namespace,
                body=job
            )
        except client.exceptions.ApiException as e:
            if e.status != 409:
                raise
    
    def _delete_job(self, name: str) -> None:
        """Delete Kubernetes Job."""
        name = resource_store.sanitize_name(name)
        try:
            self.batch_v1_api.delete_namespaced_job(
                name=name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(propagation_policy='Foreground')
            )
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise
    
    def _create_cronjob(self, name: str, spec: Dict[str, Any]) -> None:
        """Create Kubernetes CronJob for a cron harness."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        schedule_spec = spec.get('spec', {}).get('schedule', {})
        cron_expression = schedule_spec.get('cronExpression', '0 * * * *')
        
        container = self._build_container(name, spec)
        
        cronjob = client.V1CronJob(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels=labels
            ),
            spec=client.V1CronJobSpec(
                schedule=cron_expression,
                job_template=client.V1JobTemplateSpec(
                    spec=client.V1JobSpec(
                        template=client.V1PodTemplateSpec(
                            metadata=client.V1ObjectMeta(
                                labels={"agentbox.io/resource-name": name}
                            ),
                            spec=client.V1PodSpec(
                                containers=[container],
                                restart_policy='Never'
                            )
                        )
                    )
                )
            )
        )
        
        try:
            self.batch_v1_api.create_namespaced_cron_job(
                namespace=self.namespace,
                body=cronjob
            )
        except client.exceptions.ApiException as e:
            if e.status != 409:
                raise
    
    def _update_cronjob(self, name: str, spec: Dict[str, Any]) -> None:
        """Update Kubernetes CronJob."""
        name = resource_store.sanitize_name(name)
        
        try:
            cronjob = self.batch_v1_api.read_namespaced_cron_job(
                name=name,
                namespace=self.namespace
            )
            
            # Update schedule
            schedule_spec = spec.get('spec', {}).get('schedule', {})
            cron_expression = schedule_spec.get('cronExpression', '0 * * * *')
            cronjob.spec.schedule = cron_expression
            
            # Update the container
            cronjob.spec.job_template.spec.template.spec.containers = [
                self._build_container(name, spec)
            ]
            
            self.batch_v1_api.patch_namespaced_cron_job(
                name=name,
                namespace=self.namespace,
                body=cronjob
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self._create_cronjob(name, spec)
    
    def _delete_cronjob(self, name: str) -> None:
        """Delete Kubernetes CronJob."""
        name = resource_store.sanitize_name(name)
        try:
            self.batch_v1_api.delete_namespaced_cron_job(
                name=name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(propagation_policy='Foreground')
            )
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

