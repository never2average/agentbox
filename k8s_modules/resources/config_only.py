"""
Config-Only Resource Manager
Generic manager for resources that only need ConfigMap/Secret storage (no workloads)
"""
from typing import Dict, Any
from k8s_modules.base_resource import BaseResourceManager


class ConfigOnlyResourceManager(BaseResourceManager):
    """
    Manager for CRDs that only store configuration without creating workloads.

    Applicable to: model, model-autoscaler, harness-swarm-autoscaler, agent-idp,
                    tool-server-autoscaler, gateway, ai-metric, ai-meter,
                    dataset, evaluator, guardrail, tracer, recipe
    """
    
    def __init__(self, resource_group: str, kubeconfig_path: str, namespace: str = "agentbox-system"):
        """
        Initialize config-only resource manager.
        
        Args:
            resource_group: Resource group name
            kubeconfig_path: Path to kubeconfig
            namespace: Kubernetes namespace
        """
        self._resource_group = resource_group
        super().__init__(kubeconfig_path, namespace)
    
    @property
    def resource_group(self) -> str:
        """Return the resource group name."""
        return self._resource_group
    
    def _create_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        No workloads to create for config-only resources.
        
        Args:
            name: Resource name
            spec: Specification
        """
        pass
    
    def _update_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        No workloads to update for config-only resources.
        
        Args:
            name: Resource name
            spec: Specification
        """
        pass
    
    def _delete_workloads(self, name: str) -> None:
        """
        No workloads to delete for config-only resources.
        
        Args:
            name: Resource name
        """
        pass
    
    def _synthesize_status(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesize status for config-only resources.
        
        Args:
            name: Resource name
            spec: Specification
            
        Returns:
            Status dictionary (returns stored status from spec or defaults)
        """
        # Return status from spec if present, otherwise default
        if 'status' in spec:
            return spec['status']
        
        return {
            'state': 'active',
            'message': 'Configuration stored'
        }


def create_config_manager(
    resource_group: str,
    kubeconfig_path: str,
    namespace: str = "agentbox-system"
) -> ConfigOnlyResourceManager:
    """
    Create a config-only manager for a CRD resource group.

    Args:
        resource_group: CRD resource group name (e.g. "model", "ai-meter")
        kubeconfig_path: Path to kubeconfig
        namespace: Kubernetes namespace

    Returns:
        Config-only resource manager for the group
    """
    return ConfigOnlyResourceManager(resource_group, kubeconfig_path, namespace)
