"""
Kubernetes Resources Module
Handles creating, updating, and managing Kubernetes resources
"""
from kubernetes import client
from typing import Dict, Any, Optional, List
from k8s_modules.connection import get_apps_v1_api


def create_deployment(
    kubeconfig_path: str,
    name: str,
    image: str,
    namespace: str = 'default',
    replicas: int = 1,
    container_port: int = 80,
    labels: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create a deployment in the cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        name: Deployment name
        image: Container image
        namespace: Kubernetes namespace (default: 'default')
        replicas: Number of replicas (default: 1)
        container_port: Container port (default: 80)
        labels: Additional labels (optional)
        
    Returns:
        Dictionary with deployment creation result
    """
    apps_v1 = get_apps_v1_api(kubeconfig_path)
    
    if labels is None:
        labels = {"app": name}
    
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name=name, labels=labels),
        spec=client.V1DeploymentSpec(
            replicas=replicas,
            selector=client.V1LabelSelector(match_labels=labels),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=labels),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name=name,
                            image=image,
                            ports=[client.V1ContainerPort(container_port=container_port)]
                        )
                    ]
                )
            )
        )
    )
    
    try:
        result = apps_v1.create_namespaced_deployment(namespace=namespace, body=deployment)
        return {
            'success': True,
            'name': result.metadata.name,
            'namespace': result.metadata.namespace,
            'replicas': result.spec.replicas,
            'error': None
        }
    except Exception as e:
        return {
            'success': False,
            'name': name,
            'namespace': namespace,
            'replicas': replicas,
            'error': str(e)
        }


def scale_deployment(
    kubeconfig_path: str,
    name: str,
    replicas: int,
    namespace: str = 'default'
) -> Dict[str, Any]:
    """
    Scale a deployment to the specified number of replicas.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        name: Deployment name
        replicas: Desired number of replicas
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        Dictionary with scaling result
    """
    apps_v1 = get_apps_v1_api(kubeconfig_path)
    
    try:
        deployment = apps_v1.read_namespaced_deployment(name, namespace)
        deployment.spec.replicas = replicas
        result = apps_v1.patch_namespaced_deployment(name, namespace, deployment)
        
        return {
            'success': True,
            'name': result.metadata.name,
            'namespace': result.metadata.namespace,
            'replicas': result.spec.replicas,
            'error': None
        }
    except Exception as e:
        return {
            'success': False,
            'name': name,
            'namespace': namespace,
            'replicas': replicas,
            'error': str(e)
        }


def list_deployments(kubeconfig_path: str, namespace: str = 'default') -> List[Dict[str, Any]]:
    """
    List all deployments in a namespace.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        List of deployment information dictionaries
    """
    apps_v1 = get_apps_v1_api(kubeconfig_path)
    deployments = apps_v1.list_namespaced_deployment(namespace=namespace)
    
    result = []
    for deploy in deployments.items:
        result.append({
            'name': deploy.metadata.name,
            'namespace': deploy.metadata.namespace,
            'replicas': deploy.spec.replicas,
            'ready_replicas': deploy.status.ready_replicas or 0,
            'available_replicas': deploy.status.available_replicas or 0,
            'updated_replicas': deploy.status.updated_replicas or 0,
            'labels': deploy.metadata.labels or {},
            'created': str(deploy.metadata.creation_timestamp)
        })
    
    return result


def get_deployment(
    kubeconfig_path: str,
    name: str,
    namespace: str = 'default'
) -> Optional[Dict[str, Any]]:
    """
    Get details of a specific deployment.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        name: Deployment name
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        Dictionary with deployment information or None if not found
    """
    apps_v1 = get_apps_v1_api(kubeconfig_path)
    
    try:
        deploy = apps_v1.read_namespaced_deployment(name, namespace)
        return {
            'name': deploy.metadata.name,
            'namespace': deploy.metadata.namespace,
            'replicas': deploy.spec.replicas,
            'ready_replicas': deploy.status.ready_replicas or 0,
            'available_replicas': deploy.status.available_replicas or 0,
            'updated_replicas': deploy.status.updated_replicas or 0,
            'labels': deploy.metadata.labels or {},
            'selector': deploy.spec.selector.match_labels or {},
            'strategy': deploy.spec.strategy.type if deploy.spec.strategy else None,
            'created': str(deploy.metadata.creation_timestamp),
            'conditions': [
                {
                    'type': c.type,
                    'status': c.status,
                    'reason': c.reason,
                    'message': c.message
                } for c in (deploy.status.conditions or [])
            ]
        }
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return None
        raise


def delete_deployment(
    kubeconfig_path: str,
    name: str,
    namespace: str = 'default'
) -> Dict[str, Any]:
    """
    Delete a deployment from the cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        name: Deployment name
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        Dictionary with deletion result
    """
    apps_v1 = get_apps_v1_api(kubeconfig_path)
    
    try:
        apps_v1.delete_namespaced_deployment(
            name=name,
            namespace=namespace,
            body=client.V1DeleteOptions(propagation_policy='Foreground')
        )
        return {
            'success': True,
            'name': name,
            'namespace': namespace,
            'error': None
        }
    except Exception as e:
        return {
            'success': False,
            'name': name,
            'namespace': namespace,
            'error': str(e)
        }


def update_deployment_image(
    kubeconfig_path: str,
    name: str,
    container_name: str,
    new_image: str,
    namespace: str = 'default'
) -> Dict[str, Any]:
    """
    Update the container image for a deployment.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        name: Deployment name
        container_name: Container name to update
        new_image: New container image
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        Dictionary with update result
    """
    apps_v1 = get_apps_v1_api(kubeconfig_path)
    
    try:
        deployment = apps_v1.read_namespaced_deployment(name, namespace)
        
        # Update the container image
        for container in deployment.spec.template.spec.containers:
            if container.name == container_name:
                container.image = new_image
                break
        
        result = apps_v1.patch_namespaced_deployment(name, namespace, deployment)
        
        return {
            'success': True,
            'name': result.metadata.name,
            'namespace': result.metadata.namespace,
            'new_image': new_image,
            'error': None
        }
    except Exception as e:
        return {
            'success': False,
            'name': name,
            'namespace': namespace,
            'new_image': new_image,
            'error': str(e)
        }

