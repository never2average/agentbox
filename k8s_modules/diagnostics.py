"""
Kubernetes Diagnostics Module
Handles RCA, events analysis, and cluster health diagnostics
"""
from kubernetes import client
from typing import List, Dict, Any, Optional
from k8s_modules.connection import get_core_v1_api


def get_pod_issues(kubeconfig_path: str, namespace: str = 'default') -> Dict[str, List[Dict[str, Any]]]:
    """
    Analyze pods for issues (failed, pending, crashing).
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        Dictionary categorizing pods by issue type
    """
    v1 = get_core_v1_api(kubeconfig_path)
    pods = v1.list_namespaced_pod(namespace=namespace)
    
    failed_pods = []
    pending_pods = []
    crashing_pods = []
    
    for pod in pods.items:
        pod_info = {
            'name': pod.metadata.name,
            'namespace': pod.metadata.namespace,
            'status': pod.status.phase,
            'node': pod.spec.node_name,
            'conditions': []
        }
        
        # Check for failed pods
        if pod.status.phase in ['Failed', 'Unknown']:
            pod_info['reason'] = pod.status.reason
            pod_info['message'] = pod.status.message
            failed_pods.append(pod_info)
        
        # Check for pending pods
        elif pod.status.phase == 'Pending':
            if pod.status.conditions:
                for condition in pod.status.conditions:
                    if condition.status == 'False':
                        pod_info['conditions'].append({
                            'type': condition.type,
                            'reason': condition.reason,
                            'message': condition.message
                        })
            pending_pods.append(pod_info)
        
        # Check for crashing pods
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.state.waiting and 'CrashLoopBackOff' in (cs.state.waiting.reason or ''):
                    crash_info = pod_info.copy()
                    crash_info['container'] = cs.name
                    crash_info['restart_count'] = cs.restart_count
                    crash_info['reason'] = cs.state.waiting.reason
                    crash_info['message'] = cs.state.waiting.message
                    crashing_pods.append(crash_info)
    
    return {
        'failed': failed_pods,
        'pending': pending_pods,
        'crashing': crashing_pods
    }


def get_events(
    kubeconfig_path: str,
    namespace: str = 'default',
    event_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get events from a namespace, optionally filtered by type.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        event_type: Event type filter ('Warning', 'Normal', None for all)
        
    Returns:
        List of event information dictionaries
    """
    v1 = get_core_v1_api(kubeconfig_path)
    events = v1.list_namespaced_event(namespace=namespace)
    
    result = []
    for event in events.items:
        if event_type is None or event.type == event_type:
            result.append({
                'type': event.type,
                'reason': event.reason,
                'message': event.message,
                'object_name': event.involved_object.name,
                'object_kind': event.involved_object.kind,
                'count': event.count,
                'first_timestamp': str(event.first_timestamp) if event.first_timestamp else None,
                'last_timestamp': str(event.last_timestamp) if event.last_timestamp else None
            })
    
    return result


def get_warning_events(kubeconfig_path: str, namespace: str = 'default') -> List[Dict[str, Any]]:
    """
    Get warning events from a namespace.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        List of warning event information dictionaries
    """
    return get_events(kubeconfig_path, namespace, event_type='Warning')


def get_pod_logs(
    kubeconfig_path: str,
    pod_name: str,
    namespace: str = 'default',
    container: Optional[str] = None,
    tail_lines: int = 100
) -> Dict[str, Any]:
    """
    Get logs from a pod.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        pod_name: Pod name
        namespace: Kubernetes namespace (default: 'default')
        container: Container name (optional, uses first container if not specified)
        tail_lines: Number of lines to retrieve from the end (default: 100)
        
    Returns:
        Dictionary with log information
    """
    v1 = get_core_v1_api(kubeconfig_path)
    
    try:
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines
        )
        return {
            'success': True,
            'pod': pod_name,
            'namespace': namespace,
            'container': container,
            'logs': logs,
            'error': None
        }
    except Exception as e:
        return {
            'success': False,
            'pod': pod_name,
            'namespace': namespace,
            'container': container,
            'logs': None,
            'error': str(e)
        }


def analyze_node_health(kubeconfig_path: str) -> Dict[str, Any]:
    """
    Analyze the health of all nodes in the cluster.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        
    Returns:
        Dictionary with node health analysis
    """
    v1 = get_core_v1_api(kubeconfig_path)
    nodes = v1.list_node()
    
    healthy_nodes = []
    unhealthy_nodes = []
    node_issues = []
    
    for node in nodes.items:
        node_info = {
            'name': node.metadata.name,
            'version': node.status.node_info.kubelet_version,
            'os': node.status.node_info.operating_system,
            'conditions': []
        }
        
        is_ready = False
        for condition in node.status.conditions:
            node_info['conditions'].append({
                'type': condition.type,
                'status': condition.status,
                'reason': condition.reason,
                'message': condition.message
            })
            
            if condition.type == 'Ready' and condition.status == 'True':
                is_ready = True
            elif condition.status == 'True' and condition.type in ['MemoryPressure', 'DiskPressure', 'PIDPressure']:
                node_issues.append({
                    'node': node.metadata.name,
                    'issue': condition.type,
                    'reason': condition.reason,
                    'message': condition.message
                })
        
        if is_ready:
            healthy_nodes.append(node_info)
        else:
            unhealthy_nodes.append(node_info)
    
    return {
        'total_nodes': len(nodes.items),
        'healthy_count': len(healthy_nodes),
        'unhealthy_count': len(unhealthy_nodes),
        'healthy_nodes': healthy_nodes,
        'unhealthy_nodes': unhealthy_nodes,
        'node_issues': node_issues
    }


def get_resource_usage_summary(kubeconfig_path: str, namespace: str = 'default') -> Dict[str, Any]:
    """
    Get a summary of resource requests and limits in a namespace.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        Dictionary with resource usage summary
    """
    v1 = get_core_v1_api(kubeconfig_path)
    pods = v1.list_namespaced_pod(namespace=namespace)
    
    total_cpu_requests = 0
    total_memory_requests = 0
    total_cpu_limits = 0
    total_memory_limits = 0
    pod_count = 0
    
    for pod in pods.items:
        if pod.status.phase == 'Running':
            pod_count += 1
            for container in pod.spec.containers:
                if container.resources:
                    if container.resources.requests:
                        cpu_req = container.resources.requests.get('cpu', '0')
                        mem_req = container.resources.requests.get('memory', '0')
                        total_cpu_requests += parse_resource_value(cpu_req, 'cpu')
                        total_memory_requests += parse_resource_value(mem_req, 'memory')
                    
                    if container.resources.limits:
                        cpu_lim = container.resources.limits.get('cpu', '0')
                        mem_lim = container.resources.limits.get('memory', '0')
                        total_cpu_limits += parse_resource_value(cpu_lim, 'cpu')
                        total_memory_limits += parse_resource_value(mem_lim, 'memory')
    
    return {
        'namespace': namespace,
        'running_pods': pod_count,
        'total_cpu_requests': total_cpu_requests,
        'total_memory_requests_mb': total_memory_requests,
        'total_cpu_limits': total_cpu_limits,
        'total_memory_limits_mb': total_memory_limits
    }


def parse_resource_value(value: str, resource_type: str) -> float:
    """
    Parse Kubernetes resource value strings to numeric values.
    
    Args:
        value: Resource value string (e.g., '100m', '256Mi')
        resource_type: Type of resource ('cpu' or 'memory')
        
    Returns:
        Numeric value (CPU in cores, memory in MB)
    """
    if not value or value == '0':
        return 0.0
    
    try:
        if resource_type == 'cpu':
            if value.endswith('m'):
                return float(value[:-1]) / 1000
            return float(value)
        elif resource_type == 'memory':
            if value.endswith('Ki'):
                return float(value[:-2]) / 1024
            elif value.endswith('Mi'):
                return float(value[:-2])
            elif value.endswith('Gi'):
                return float(value[:-2]) * 1024
            elif value.endswith('K'):
                return float(value[:-1]) / 1024
            elif value.endswith('M'):
                return float(value[:-1])
            elif value.endswith('G'):
                return float(value[:-1]) * 1024
            return float(value) / (1024 * 1024)  # Assume bytes
    except (ValueError, AttributeError):
        return 0.0
    
    return 0.0


def perform_rca(kubeconfig_path: str, namespace: str = 'default') -> Dict[str, Any]:
    """
    Perform comprehensive Root Cause Analysis on a namespace.
    
    Args:
        kubeconfig_path: Path to the kubeconfig file
        namespace: Kubernetes namespace (default: 'default')
        
    Returns:
        Dictionary with comprehensive RCA results
    """
    pod_issues = get_pod_issues(kubeconfig_path, namespace)
    warning_events = get_warning_events(kubeconfig_path, namespace)
    node_health = analyze_node_health(kubeconfig_path)
    
    total_issues = (
        len(pod_issues['failed']) +
        len(pod_issues['pending']) +
        len(pod_issues['crashing'])
    )
    
    return {
        'namespace': namespace,
        'total_issues_found': total_issues,
        'pod_issues': pod_issues,
        'warning_events': warning_events,
        'node_health': node_health,
        'recommendations': generate_recommendations(pod_issues, warning_events, node_health)
    }


def generate_recommendations(
    pod_issues: Dict[str, List[Dict[str, Any]]],
    warning_events: List[Dict[str, Any]],
    node_health: Dict[str, Any]
) -> List[str]:
    """
    Generate actionable recommendations based on diagnostics.
    
    Args:
        pod_issues: Pod issues from get_pod_issues()
        warning_events: Warning events from get_warning_events()
        node_health: Node health from analyze_node_health()
        
    Returns:
        List of recommendation strings
    """
    recommendations = []
    
    if pod_issues['crashing']:
        recommendations.append("Check pod logs for crashing containers: kubectl logs <pod-name>")
        recommendations.append("Review resource limits and requests for crashing pods")
        recommendations.append("Verify container image availability and pull secrets")
    
    if pod_issues['pending']:
        recommendations.append("Check node resources: kubectl describe nodes")
        recommendations.append("Verify pod scheduling constraints (nodeSelector, affinity, taints)")
        recommendations.append("Review PersistentVolumeClaim status if storage is involved")
    
    if pod_issues['failed']:
        recommendations.append("Investigate failed pod events: kubectl describe pod <pod-name>")
        recommendations.append("Check for configuration errors in pod specifications")
    
    if node_health['unhealthy_count'] > 0:
        recommendations.append(f"Investigate {node_health['unhealthy_count']} unhealthy nodes")
        recommendations.append("Check node system logs and resource utilization")
    
    if node_health['node_issues']:
        recommendations.append("Address node pressure issues (Memory/Disk/PID pressure)")
        recommendations.append("Consider scaling cluster or optimizing resource usage")
    
    if len(warning_events) > 10:
        recommendations.append("High number of warning events detected - review cluster stability")
        recommendations.append("Check for recurring patterns in warning messages")
    
    if not recommendations:
        recommendations.append("No critical issues detected - cluster appears healthy")
    
    return recommendations

