"""
Background Resource Manager
Manages background task resources backed by Kubernetes Jobs and CronJobs
"""
from typing import Dict, Any
from kubernetes import client
from k8s_modules.base_resource import BaseResourceManager
from k8s_modules import resource_store


class BackgroundManager(BaseResourceManager):
    """
    Manager for background task resources.
    
    Maps to Kubernetes workloads:
    - If schedule is defined -> CronJob
    - Otherwise -> Job
    """
    
    @property
    def resource_group(self) -> str:
        """Return the resource group name."""
        return "background"
    
    def _has_schedule(self, spec: Dict[str, Any]) -> bool:
        """Check if spec has a schedule defined."""
        schedule = spec.get('schedule')
        if not schedule:
            return False
        
        schedule_type = schedule.get('type')
        return schedule_type in ['cron', 'interval']
    
    def _create_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Create Kubernetes workloads for background task.
        
        Args:
            name: Task name
            spec: Task specification
        """
        if self._has_schedule(spec):
            self._create_cronjob(name, spec)
        else:
            self._create_job(name, spec)
    
    def _update_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Update Kubernetes workloads for background task.
        
        Args:
            name: Task name
            spec: Updated specification
        """
        # Delete old workload and create new one
        # (switching between Job and CronJob requires this)
        self._delete_workloads(name)
        self._create_workloads(name, spec)
    
    def _delete_workloads(self, name: str) -> None:
        """
        Delete Kubernetes workloads for background task.
        
        Args:
            name: Task name
        """
        self._delete_job(name)
        self._delete_cronjob(name)
    
    def _synthesize_status(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesize status from Kubernetes workloads.
        
        Args:
            name: Task name
            spec: Specification
            
        Returns:
            Status dictionary
        """
        name = resource_store.sanitize_name(name)
        
        status = {
            'state': 'unknown',
            'message': 'No workload found'
        }
        
        try:
            if self._has_schedule(spec):
                # Check CronJob status
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
            else:
                # Check Job status
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
    
    def _create_job(self, name: str, spec: Dict[str, Any]) -> None:
        """Create Kubernetes Job for background task."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        # Extract task configuration
        task = spec.get('task', {})
        image = task.get('image', 'busybox:latest')
        command = task.get('command', [])
        args = task.get('args', [])
        env = task.get('env', {})
        
        # Build container
        container = client.V1Container(
            name=name,
            image=image,
            env=[
                client.V1EnvVar(name="AGENTBOX_RESOURCE_NAME", value=name),
                client.V1EnvVar(name="AGENTBOX_RESOURCE_GROUP", value=self.resource_group)
            ]
        )
        
        # Add custom environment variables
        for key, value in env.items():
            container.env.append(client.V1EnvVar(name=key, value=str(value)))
        
        if command:
            container.command = command
        if args:
            container.args = args
        
        # Create job
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
                backoff_limit=spec.get('retry', {}).get('max_attempts', 3)
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
        """Create Kubernetes CronJob for scheduled background task."""
        name = resource_store.sanitize_name(name)
        labels = resource_store.create_resource_labels(self.resource_group, name)
        
        # Extract schedule
        schedule_spec = spec.get('schedule', {})
        schedule_type = schedule_spec.get('type', 'cron')
        
        if schedule_type == 'cron':
            cron_expression = schedule_spec.get('cron_expression', '0 * * * *')
        elif schedule_type == 'interval':
            # Convert interval to cron (simplified)
            interval_minutes = schedule_spec.get('interval_minutes', 60)
            if interval_minutes >= 60:
                hours = interval_minutes // 60
                cron_expression = f"0 */{hours} * * *"
            else:
                cron_expression = f"*/{interval_minutes} * * * *"
        else:
            cron_expression = '0 * * * *'
        
        # Extract task configuration
        task = spec.get('task', {})
        image = task.get('image', 'busybox:latest')
        command = task.get('command', [])
        args = task.get('args', [])
        env = task.get('env', {})
        
        # Build container
        container = client.V1Container(
            name=name,
            image=image,
            env=[
                client.V1EnvVar(name="AGENTBOX_RESOURCE_NAME", value=name),
                client.V1EnvVar(name="AGENTBOX_RESOURCE_GROUP", value=self.resource_group)
            ]
        )
        
        # Add custom environment variables
        for key, value in env.items():
            container.env.append(client.V1EnvVar(name=key, value=str(value)))
        
        if command:
            container.command = command
        if args:
            container.args = args
        
        # Create cronjob
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
                        ),
                        backoff_limit=spec.get('retry', {}).get('max_attempts', 3)
                    )
                ),
                successful_jobs_history_limit=3,
                failed_jobs_history_limit=3
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

