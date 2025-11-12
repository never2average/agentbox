"""
Kubernetes Connection Module
Handles kubeconfig loading and connection verification
"""
from kubernetes import client, config as k8s_config
from typing import Optional, Dict, Any


def load_kubeconfig(kubeconfig_path: str) -> None:
    """
    Load Kubernetes configuration from kubeconfig file.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Raises:
        Exception: If kubeconfig cannot be loaded
    """
    k8s_config.load_kube_config(config_file=kubeconfig_path)


def verify_connection(kubeconfig_path: str) -> Dict[str, Any]:
    """
    Verify connection to Kubernetes cluster and get version info.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        Dictionary with connection status and cluster information
        
    Raises:
        Exception: If connection cannot be verified
    """
    try:
        load_kubeconfig(kubeconfig_path)
        version_api = client.VersionApi()
        version = version_api.get_code()
        
        return {
            'connected': True,
            'version': version.git_version,
            'platform': version.platform,
            'error': None
        }
    except Exception as e:
        return {
            'connected': False,
            'version': None,
            'platform': None,
            'error': str(e)
        }


def get_core_v1_api(kubeconfig_path: str) -> client.CoreV1Api:
    """
    Get CoreV1Api client for Kubernetes cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        CoreV1Api client instance
    """
    load_kubeconfig(kubeconfig_path)
    return client.CoreV1Api()


def get_apps_v1_api(kubeconfig_path: str) -> client.AppsV1Api:
    """
    Get AppsV1Api client for Kubernetes cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        AppsV1Api client instance
    """
    load_kubeconfig(kubeconfig_path)
    return client.AppsV1Api()


def get_version_api(kubeconfig_path: str) -> client.VersionApi:
    """
    Get VersionApi client for Kubernetes cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        VersionApi client instance
    """
    load_kubeconfig(kubeconfig_path)
    return client.VersionApi()

