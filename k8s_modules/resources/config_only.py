"""
Config-Only Resource Manager
Generic manager for resources that only need ConfigMap/Secret storage (no workloads)
"""
from typing import Dict, Any
from k8s_modules.base_resource import BaseResourceManager


class ConfigOnlyResourceManager(BaseResourceManager):
    """
    Manager for resources that only store configuration without creating workloads.
    
    Applicable to: agents, channels, gateways, governance, hardware, io, logs,
                    metric, models, notifications, policies, recipe, escalations, evals
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


# Factory functions for each config-only resource type

def create_agents_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for agents resources."""
    return ConfigOnlyResourceManager("agents", kubeconfig_path, namespace)


def create_channels_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for channels resources."""
    return ConfigOnlyResourceManager("channels", kubeconfig_path, namespace)


def create_gateways_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for gateways resources."""
    return ConfigOnlyResourceManager("gateways", kubeconfig_path, namespace)


def create_governance_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for governance resources."""
    return ConfigOnlyResourceManager("governance", kubeconfig_path, namespace)


def create_hardware_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for hardware resources."""
    return ConfigOnlyResourceManager("hardware", kubeconfig_path, namespace)


def create_io_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for io resources."""
    return ConfigOnlyResourceManager("io", kubeconfig_path, namespace)


def create_logs_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for logs resources."""
    return ConfigOnlyResourceManager("logs", kubeconfig_path, namespace)


def create_metric_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for metric resources."""
    return ConfigOnlyResourceManager("metric", kubeconfig_path, namespace)


def create_models_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for models resources."""
    return ConfigOnlyResourceManager("models", kubeconfig_path, namespace)


def create_notifications_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for notifications resources."""
    return ConfigOnlyResourceManager("notifications", kubeconfig_path, namespace)


def create_policies_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for policies resources."""
    return ConfigOnlyResourceManager("policies", kubeconfig_path, namespace)


def create_recipe_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for recipe resources."""
    return ConfigOnlyResourceManager("recipe", kubeconfig_path, namespace)


def create_escalations_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for escalations resources."""
    return ConfigOnlyResourceManager("escalations", kubeconfig_path, namespace)


def create_evals_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for evals resources."""
    return ConfigOnlyResourceManager("evals", kubeconfig_path, namespace)


def create_tools_manager(kubeconfig_path: str, namespace: str = "agentbox-system") -> ConfigOnlyResourceManager:
    """Create manager for tools resources."""
    return ConfigOnlyResourceManager("tools", kubeconfig_path, namespace)

