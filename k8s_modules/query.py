"""
Kubernetes Query Module
Handles querying cluster resources (pods, services, nodes, namespaces)
"""
from kubernetes import client
from typing import List, Dict, Any, Optional
from k8s_modules.connection import get_core_v1_api


def list_pods(kubeconfig_path: str, namespace: str = 'default') -> List[Dict[str, Any]]:
    """
    List all pods in a namespace.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        List of pod information dictionaries
    """
    v1 = get_core_v1_api(kubeconfig_path)
    pods = v1.list_namespaced_pod(namespace=namespace)
    
    result = []
    for pod in pods.items:
        result.append({
            'name': pod.metadata.name,
            'namespace': pod.metadata.namespace,
            'status': pod.status.phase,
            'ready': sum(1 for cs in (pod.status.container_statuses or []) if cs.ready),
            'total_containers': len(pod.spec.containers),
            'node': pod.spec.node_name,
            'pod_ip': pod.status.pod_ip,
            'conditions': [
                {
                    'type': c.type,
                    'status': c.status,
                    'reason': c.reason,
                    'message': c.message
                } for c in (pod.status.conditions or [])
            ],
            'container_statuses': [
                {
                    'name': cs.name,
                    'ready': cs.ready,
                    'restart_count': cs.restart_count,
                    'state': get_container_state(cs)
                } for cs in (pod.status.container_statuses or [])
            ]
        })
    
    return result


def get_container_state(container_status) -> Dict[str, Any]:
    """
    Extract container state information.
    
    Args:
        container_status: Container status object
        
    Returns:
        Dictionary with state information
    """
    if container_status.state.running:
        return {
            'type': 'running',
            'started_at': str(container_status.state.running.started_at)
        }
    elif container_status.state.waiting:
        return {
            'type': 'waiting',
            'reason': container_status.state.waiting.reason,
            'message': container_status.state.waiting.message
        }
    elif container_status.state.terminated:
        return {
            'type': 'terminated',
            'reason': container_status.state.terminated.reason,
            'exit_code': container_status.state.terminated.exit_code,
            'message': container_status.state.terminated.message
        }
    return {'type': 'unknown'}


def list_services(kubeconfig_path: str, namespace: str = 'default') -> List[Dict[str, Any]]:
    """
    List all services in a namespace.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        List of service information dictionaries
    """
    v1 = get_core_v1_api(kubeconfig_path)
    services = v1.list_namespaced_service(namespace=namespace)
    
    result = []
    for svc in services.items:
        result.append({
            'name': svc.metadata.name,
            'namespace': svc.metadata.namespace,
            'type': svc.spec.type,
            'cluster_ip': svc.spec.cluster_ip,
            'ports': [
                {
                    'port': p.port,
                    'target_port': str(p.target_port),
                    'protocol': p.protocol,
                    'node_port': p.node_port
                } for p in (svc.spec.ports or [])
            ],
            'selector': svc.spec.selector or {}
        })
    
    return result


def list_nodes(kubeconfig_path: str) -> List[Dict[str, Any]]:
    """
    List all nodes in the cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        List of node information dictionaries
    """
    v1 = get_core_v1_api(kubeconfig_path)
    nodes = v1.list_node()
    
    result = []
    for node in nodes.items:
        is_ready = any(
            c.type == 'Ready' and c.status == 'True'
            for c in node.status.conditions
        )
        
        result.append({
            'name': node.metadata.name,
            'status': 'Ready' if is_ready else 'NotReady',
            'roles': list(node.metadata.labels.keys()) if node.metadata.labels else [],
            'version': node.status.node_info.kubelet_version,
            'os': node.status.node_info.operating_system,
            'kernel': node.status.node_info.kernel_version,
            'conditions': [
                {
                    'type': c.type,
                    'status': c.status,
                    'reason': c.reason,
                    'message': c.message
                } for c in (node.status.conditions or [])
            ]
        })
    
    return result


def list_namespaces(kubeconfig_path: str) -> List[Dict[str, Any]]:
    """
    List all namespaces in the cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        List of namespace information dictionaries
    """
    v1 = get_core_v1_api(kubeconfig_path)
    namespaces = v1.list_namespace()
    
    result = []
    for ns in namespaces.items:
        result.append({
            'name': ns.metadata.name,
            'status': ns.status.phase,
            'created': str(ns.metadata.creation_timestamp)
        })
    
    return result


def get_cluster_overview(kubeconfig_path: str) -> Dict[str, Any]:
    """
    Get a high-level overview of the cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        Dictionary with cluster overview information
    """
    v1 = get_core_v1_api(kubeconfig_path)
    
    namespaces = v1.list_namespace()
    nodes = v1.list_node()
    
    # Count pods across all namespaces
    all_pods = v1.list_pod_for_all_namespaces()
    running_pods = sum(1 for p in all_pods.items if p.status.phase == 'Running')
    
    return {
        'namespace_count': len(namespaces.items),
        'node_count': len(nodes.items),
        'total_pods': len(all_pods.items),
        'running_pods': running_pods,
        'nodes_ready': sum(
            1 for n in nodes.items
            if any(c.type == 'Ready' and c.status == 'True' for c in n.status.conditions)
        )
    }

