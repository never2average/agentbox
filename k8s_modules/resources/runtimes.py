"""
Runtimes Resource Manager
Manages runtime resources backed by Kubernetes Deployments, Services, Jobs, and CronJobs
"""
from typing import Dict, Any, Optional
from kubernetes import client
from k8s_modules.base_resource import BaseResourceManager
from k8s_modules import resource_store


class RuntimesManager(BaseResourceManager):
    """
    Manager for runtime resources.
    
    Maps runtime kinds to Kubernetes workloads:
    - server/worker -> Deployment (+ Service if endpoints present)
    - batch -> Job
    - cron -> CronJob
    """
    
    @property
    def resource_group(self) -> str:
        """Return the resource group name."""
        return "runtimes"
    
    def _create_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Create Kubernetes workloads for runtime.
        
        Args:
            name: Runtime name
            spec: Runtime specification
        """
        runtime_kind = spec.get('metadata', {}).get('kind', 'server')
        
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
        runtime_kind = spec.get('metadata', {}).get('kind', 'server')
        
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
        runtime_kind = spec.get('metadata', {}).get('kind', 'server')
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
                    'ready_replicas': ready_replicas,
                    'available_replicas': available_replicas,
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
                    'start_time': str(job.status.start_time) if job.status.start_time else None,
                    'completion_time': str(job.status.completion_time) if job.status.completion_time else None
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
                    'active_jobs': active_jobs,
                    'last_schedule_time': str(last_schedule_time) if last_schedule_time else None,
                    'last_successful_time': str(last_successful_time) if last_successful_time else None,
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
    
    def _create_deployment(self, name: str, spec: Dict[str, Any]) -> None:
        """Create Kubernetes Deployment for runtime."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        # Extract compute resources
        compute = spec.get('spec', {}).get('compute', {})
        cpu_spec = compute.get('cpu', {})
        memory_mb = cpu_spec.get('memory_mb', 512)
        cpu_cores = cpu_spec.get('cores', 1)
        
        # Extract code configuration
        code = spec.get('spec', {}).get('code', {})
        image = code.get('image', 'busybox:latest')
        entrypoint = code.get('entrypoint')
        args = code.get('args', [])
        
        # Build container
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
        
        # Add environment variables from ConfigMap/Secret
        container.env = [
            client.V1EnvVar(
                name="AGENTBOX_RESOURCE_NAME",
                value=name
            ),
            client.V1EnvVar(
                name="AGENTBOX_RESOURCE_GROUP",
                value=self.resource_group
            )
        ]
        
        # Create deployment
        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels=labels
            ),
            spec=client.V1DeploymentSpec(
                replicas=1,
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
        """Update Kubernetes Deployment for runtime."""
        name = resource_store.sanitize_name(name)
        
        try:
            # Read existing deployment
            deployment = self.apps_v1_api.read_namespaced_deployment(
                name=name,
                namespace=self.namespace
            )
            
            # Update image and resources
            code = spec.get('spec', {}).get('code', {})
            image = code.get('image', 'busybox:latest')
            
            compute = spec.get('spec', {}).get('compute', {})
            cpu_spec = compute.get('cpu', {})
            memory_mb = cpu_spec.get('memory_mb', 512)
            cpu_cores = cpu_spec.get('cores', 1)
            
            container = deployment.spec.template.spec.containers[0]
            container.image = image
            container.resources = client.V1ResourceRequirements(
                requests={
                    'cpu': f"{cpu_cores}",
                    'memory': f"{memory_mb}Mi"
                },
                limits={
                    'cpu': f"{cpu_cores * 2}",
                    'memory': f"{memory_mb * 2}Mi"
                }
            )
            
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
        
        # Extract ports from endpoints
        ports = []
        for endpoint in endpoints:
            interface = endpoint.get('interface', 'http')
            path = endpoint.get('path', '/')
            
            # Map interface to port
            if interface == 'http':
                port = 80
            elif interface == 'grpc':
                port = 9090
            elif interface == 'websocket':
                port = 8080
            else:
                port = 80
            
            ports.append(
                client.V1ServicePort(
                    name=interface,
                    port=port,
                    target_port=port,
                    protocol='TCP'
                )
            )
        
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
        """Create Kubernetes Job for batch runtime."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        # Extract code configuration
        code = spec.get('spec', {}).get('code', {})
        image = code.get('image', 'busybox:latest')
        entrypoint = code.get('entrypoint')
        args = code.get('args', [])
        
        container = client.V1Container(
            name=name,
            image=image,
            env=[
                client.V1EnvVar(name="AGENTBOX_RESOURCE_NAME", value=name),
                client.V1EnvVar(name="AGENTBOX_RESOURCE_GROUP", value=self.resource_group)
            ]
        )
        
        if entrypoint:
            container.command = [entrypoint]
        if args:
            container.args = args
        
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
        """Create Kubernetes CronJob for cron runtime."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        # Extract schedule
        schedule_spec = spec.get('spec', {}).get('schedule', {})
        cron_expression = schedule_spec.get('cron_expression', '0 * * * *')
        
        # Extract code configuration
        code = spec.get('spec', {}).get('code', {})
        image = code.get('image', 'busybox:latest')
        entrypoint = code.get('entrypoint')
        args = code.get('args', [])
        
        container = client.V1Container(
            name=name,
            image=image,
            env=[
                client.V1EnvVar(name="AGENTBOX_RESOURCE_NAME", value=name),
                client.V1EnvVar(name="AGENTBOX_RESOURCE_GROUP", value=self.resource_group)
            ]
        )
        
        if entrypoint:
            container.command = [entrypoint]
        if args:
            container.args = args
        
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
            cron_expression = schedule_spec.get('cron_expression', '0 * * * *')
            cronjob.spec.schedule = cron_expression
            
            # Update image
            code = spec.get('spec', {}).get('code', {})
            image = code.get('image', 'busybox:latest')
            cronjob.spec.job_template.spec.template.spec.containers[0].image = image
            
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

