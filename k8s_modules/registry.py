"""
Resource Registry Module
Central registry for mapping resource groups to manager classes
"""
from typing import Dict, Type, Optional
from k8s_modules.base_resource import BaseResourceManager
from k8s_modules.resources.config_only import (
    create_agents_manager,
    create_channels_manager,
    create_gateways_manager,
    create_governance_manager,
    create_hardware_manager,
    create_io_manager,
    create_logs_manager,
    create_metric_manager,
    create_models_manager,
    create_notifications_manager,
    create_policies_manager,
    create_recipe_manager,
    create_escalations_manager,
    create_evals_manager,
    create_tools_manager
)
from k8s_modules.resources.runtimes import RuntimesManager
from k8s_modules.resources.background import BackgroundManager


# Registry mapping resource groups to factory functions
_REGISTRY: Dict[str, any] = {
    # Config-only resources
    'agents': create_agents_manager,
    'channels': create_channels_manager,
    'gateways': create_gateways_manager,
    'governance': create_governance_manager,
    'hardware': create_hardware_manager,
    'io': create_io_manager,
    'logs': create_logs_manager,
    'metric': create_metric_manager,
    'models': create_models_manager,
    'notifications': create_notifications_manager,
    'policies': create_policies_manager,
    'recipe': create_recipe_manager,
    'escalations': create_escalations_manager,
    'evals': create_evals_manager,
    'tools': create_tools_manager,
    
    # Workload resources
    'runtimes': RuntimesManager,
    'background': BackgroundManager
}


def get_manager(
    resource_group: str,
    kubeconfig_path: str,
    namespace: str = "agentbox-system"
) -> BaseResourceManager:
    """
    Get a resource manager for the specified resource group.
    
    Args:
        resource_group: Resource group name (e.g., "runtimes", "agents")
        kubeconfig_path: Path to kubeconfig file
        namespace: Kubernetes namespace (default: agentbox-system)
        
    Returns:
        Resource manager instance
        
    Raises:
        ValueError: If resource group is not registered
        
    Examples:
        >>> mgr = get_manager("runtimes", "/path/to/kubeconfig")
        >>> mgr.create(runtime_spec)
        
        >>> mgr = get_manager("agents", "/path/to/kubeconfig")
        >>> agents = mgr.list()
    """
    if resource_group not in _REGISTRY:
        available = ', '.join(sorted(_REGISTRY.keys()))
        raise ValueError(
            f"Unknown resource group '{resource_group}'. "
            f"Available groups: {available}"
        )
    
    factory = _REGISTRY[resource_group]
    
    # Check if it's a class or factory function
    if isinstance(factory, type):
        # It's a class, instantiate it
        return factory(kubeconfig_path, namespace)
    else:
        # It's a factory function, call it
        return factory(kubeconfig_path, namespace)


def list_resource_groups() -> list:
    """
    List all registered resource groups.
    
    Returns:
        List of resource group names
        
    Examples:
        >>> groups = list_resource_groups()
        >>> print(groups)
        ['agents', 'background', 'channels', ...]
    """
    return sorted(_REGISTRY.keys())


def register_resource_group(
    resource_group: str,
    manager_class_or_factory: any
) -> None:
    """
    Register a custom resource group with a manager class or factory.
    
    Args:
        resource_group: Resource group name
        manager_class_or_factory: Manager class or factory function
        
    Examples:
        >>> class CustomManager(BaseResourceManager):
        ...     pass
        >>> register_resource_group("custom", CustomManager)
    """
    _REGISTRY[resource_group] = manager_class_or_factory


def unregister_resource_group(resource_group: str) -> None:
    """
    Unregister a resource group.
    
    Args:
        resource_group: Resource group name
    """
    if resource_group in _REGISTRY:
        del _REGISTRY[resource_group]


class ResourceRegistry:
    """
    Object-oriented interface to the resource registry.
    
    Examples:
        >>> registry = ResourceRegistry("/path/to/kubeconfig")
        >>> runtimes_mgr = registry.get("runtimes")
        >>> runtimes_mgr.create(spec)
    """
    
    def __init__(self, kubeconfig_path: str, namespace: str = "agentbox-system"):
        """
        Initialize resource registry.
        
        Args:
            kubeconfig_path: Path to kubeconfig file
            namespace: Kubernetes namespace
        """
        self.kubeconfig_path = kubeconfig_path
        self.namespace = namespace
        self._managers: Dict[str, BaseResourceManager] = {}
    
    def get(self, resource_group: str) -> BaseResourceManager:
        """
        Get or create a manager for the resource group.
        
        Args:
            resource_group: Resource group name
            
        Returns:
            Resource manager instance
        """
        if resource_group not in self._managers:
            self._managers[resource_group] = get_manager(
                resource_group,
                self.kubeconfig_path,
                self.namespace
            )
        return self._managers[resource_group]
    
    def list_groups(self) -> list:
        """
        List all available resource groups.
        
        Returns:
            List of resource group names
        """
        return list_resource_groups()
    
    def clear_cache(self) -> None:
        """Clear cached manager instances."""
        self._managers.clear()


# Convenience functions for common operations

def create_resource(
    resource_group: str,
    spec: Dict,
    kubeconfig_path: str,
    namespace: str = "agentbox-system",
    secret_fields: Optional[list] = None
) -> Dict:
    """
    Convenience function to create a resource.
    
    Args:
        resource_group: Resource group name
        spec: Resource specification
        kubeconfig_path: Path to kubeconfig
        namespace: Kubernetes namespace
        secret_fields: Optional list of secret field paths
        
    Returns:
        Created resource with status
    """
    mgr = get_manager(resource_group, kubeconfig_path, namespace)
    return mgr.create(spec, secret_fields=secret_fields)


def get_resource(
    resource_group: str,
    name: str,
    kubeconfig_path: str,
    namespace: str = "agentbox-system",
    include_secrets: bool = False
) -> Optional[Dict]:
    """
    Convenience function to get a resource.
    
    Args:
        resource_group: Resource group name
        name: Resource name
        kubeconfig_path: Path to kubeconfig
        namespace: Kubernetes namespace
        include_secrets: Whether to include secret fields
        
    Returns:
        Resource dictionary or None
    """
    mgr = get_manager(resource_group, kubeconfig_path, namespace)
    return mgr.get(name, include_secrets=include_secrets)


def update_resource(
    resource_group: str,
    name: str,
    spec: Dict,
    kubeconfig_path: str,
    namespace: str = "agentbox-system",
    strategy: str = "merge",
    secret_fields: Optional[list] = None
) -> Dict:
    """
    Convenience function to update a resource.
    
    Args:
        resource_group: Resource group name
        name: Resource name
        spec: New or partial specification
        kubeconfig_path: Path to kubeconfig
        namespace: Kubernetes namespace
        strategy: "merge" or "replace"
        secret_fields: Optional list of secret field paths
        
    Returns:
        Updated resource with status
    """
    mgr = get_manager(resource_group, kubeconfig_path, namespace)
    return mgr.update(name, spec, strategy, secret_fields=secret_fields)


def delete_resource(
    resource_group: str,
    name: str,
    kubeconfig_path: str,
    namespace: str = "agentbox-system"
) -> None:
    """
    Convenience function to delete a resource.
    
    Args:
        resource_group: Resource group name
        name: Resource name
        kubeconfig_path: Path to kubeconfig
        namespace: Kubernetes namespace
    """
    mgr = get_manager(resource_group, kubeconfig_path, namespace)
    mgr.delete(name)


def list_resources(
    resource_group: str,
    kubeconfig_path: str,
    namespace: str = "agentbox-system",
    selector: Optional[Dict[str, str]] = None
) -> list:
    """
    Convenience function to list resources.
    
    Args:
        resource_group: Resource group name
        kubeconfig_path: Path to kubeconfig
        namespace: Kubernetes namespace
        selector: Optional label selector
        
    Returns:
        List of resources with status
    """
    mgr = get_manager(resource_group, kubeconfig_path, namespace)
    return mgr.list(selector)

