"""
Kubernetes Resource Store Module
Handles ConfigMap and Secret persistence with labels and naming conventions
"""
import json
import re
from typing import Dict, Any, List, Optional, Tuple
from kubernetes import client


NAMESPACE = "agentbox-system"
COMMON_LABELS = {
    "app.kubernetes.io/part-of": "agentbox",
    "agentbox.io/managed-by": "ai-ctl"
}


def sanitize_name(name: str) -> str:
    """
    Sanitize a name to be DNS-1123 compliant.
    
    Args:
        name: Raw name string
        
    Returns:
        DNS-1123 compliant name (lowercase alphanumeric and hyphens, max 63 chars)
    """
    # Convert to lowercase and replace invalid chars with hyphens
    sanitized = re.sub(r'[^a-z0-9-]', '-', name.lower())
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    # Collapse multiple hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    # Truncate to 63 chars
    return sanitized[:63]


def get_value_at_path(data: Dict[str, Any], path: str) -> Any:
    """
    Get value from nested dict using dot notation path.
    
    Args:
        data: Dictionary to traverse
        path: Dot-separated path (e.g., "spec.code.repo.token")
        
    Returns:
        Value at path or None if not found
    """
    keys = path.split('.')
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def set_value_at_path(data: Dict[str, Any], path: str, value: Any) -> None:
    """
    Set value in nested dict using dot notation path.
    
    Args:
        data: Dictionary to modify
        path: Dot-separated path
        value: Value to set
    """
    keys = path.split('.')
    current = data
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def delete_value_at_path(data: Dict[str, Any], path: str) -> None:
    """
    Delete value from nested dict using dot notation path.
    
    Args:
        data: Dictionary to modify
        path: Dot-separated path
    """
    keys = path.split('.')
    current = data
    for key in keys[:-1]:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return
    if isinstance(current, dict) and keys[-1] in current:
        del current[keys[-1]]


# Field names that hold credentials, in normalized snake_case form
SECRET_FIELD_NAMES = frozenset([
    'password', 'passwd', 'secret', 'token', 'credential', 'credentials',
    'api_key', 'apikey', 'access_key', 'secret_key', 'private_key',
    'secret_access_key', 'auth_token', 'bearer_token', 'client_secret',
    'signing_key', 'encryption_key', 'session_key', 'webhook_secret',
])

SECRET_FIELD_SUFFIXES = (
    '_password', '_passwd', '_secret', '_token', '_credential', '_credentials',
    '_api_key', '_apikey', '_access_key', '_secret_key', '_private_key',
    '_auth_token', '_signing_key', '_encryption_key',
)


def normalize_field_name(field_name: str) -> str:
    """
    Normalize a field name to snake_case so camelCase and snake_case match alike.

    Args:
        field_name: Field name in any casing (apiKey, api_key, APIKey)

    Returns:
        Lowercase snake_case form of the name
    """
    normalized = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', field_name)
    normalized = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', '_', normalized)
    return normalized.replace('-', '_').lower()


def is_secret_field_by_convention(field_name: str) -> bool:
    """
    Check if a field name suggests it contains sensitive data.

    Matches camelCase and snake_case equally, and deliberately does not match
    names that merely contain "key" or "token" — stateKey and totalTokens are
    not credentials.

    Args:
        field_name: Field name to check

    Returns:
        True if field name suggests sensitive data
    """
    normalized = normalize_field_name(field_name)
    return (normalized in SECRET_FIELD_NAMES
            or normalized.endswith(SECRET_FIELD_SUFFIXES))


def extract_secret_fields(spec: Dict[str, Any], explicit_paths: Optional[List[str]] = None) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Extract sensitive fields from spec into separate dict.
    
    Args:
        spec: Full specification dictionary
        explicit_paths: Optional list of dot-notation paths to treat as secret
        
    Returns:
        Tuple of (public_spec, secret_data) where secret_data maps path -> value
    """
    import copy
    public_spec = copy.deepcopy(spec)
    secret_data = {}
    
    explicit_paths = explicit_paths or []
    
    # Extract explicitly marked paths
    for path in explicit_paths:
        value = get_value_at_path(public_spec, path)
        if value is not None:
            secret_data[path] = str(value)
            delete_value_at_path(public_spec, path)
    
    # Recursively scan for convention-based secrets
    def scan_for_secrets(data: Dict[str, Any], current_path: str = ""):
        if not isinstance(data, dict):
            return
        
        keys_to_remove = []
        for key, value in data.items():
            full_path = f"{current_path}.{key}" if current_path else key
            
            # Skip if already extracted explicitly
            if full_path in explicit_paths:
                continue
            
            if is_secret_field_by_convention(key) and value is not None:
                secret_data[full_path] = str(value)
                keys_to_remove.append(key)
            elif isinstance(value, dict):
                scan_for_secrets(value, full_path)
        
        for key in keys_to_remove:
            del data[key]
    
    scan_for_secrets(public_spec)
    
    return public_spec, secret_data


def create_resource_labels(resource_group: str, resource_name: str) -> Dict[str, str]:
    """
    Create standard labels for a resource.
    
    Args:
        resource_group: Resource group (e.g., "runtimes", "agents")
        resource_name: Resource name
        
    Returns:
        Dictionary of labels
    """
    labels = COMMON_LABELS.copy()
    labels.update({
        "agentbox.io/resource-group": resource_group,
        "agentbox.io/resource-name": sanitize_name(resource_name)
    })
    return labels


def create_configmap(
    core_v1_api: client.CoreV1Api,
    name: str,
    resource_group: str,
    spec: Dict[str, Any],
    namespace: str = NAMESPACE
) -> client.V1ConfigMap:
    """
    Create a ConfigMap to store resource specification.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: ConfigMap name
        resource_group: Resource group
        spec: Specification dictionary
        namespace: Kubernetes namespace
        
    Returns:
        Created ConfigMap object
    """
    labels = create_resource_labels(resource_group, name)
    
    configmap = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(
            name=sanitize_name(name),
            namespace=namespace,
            labels=labels
        ),
        data={
            "spec.json": json.dumps(spec, indent=2)
        }
    )
    
    return core_v1_api.create_namespaced_config_map(namespace=namespace, body=configmap)


def create_secret(
    core_v1_api: client.CoreV1Api,
    name: str,
    resource_group: str,
    secret_data: Dict[str, str],
    namespace: str = NAMESPACE
) -> Optional[client.V1Secret]:
    """
    Create a Secret to store sensitive fields.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: Secret name
        resource_group: Resource group
        secret_data: Dictionary mapping field paths to values
        namespace: Kubernetes namespace
        
    Returns:
        Created Secret object or None if no secret data
    """
    if not secret_data:
        return None
    
    labels = create_resource_labels(resource_group, name)
    
    # Encode secret data as base64 is handled by kubernetes client
    string_data = {k.replace('.', '_'): v for k, v in secret_data.items()}
    
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=f"{sanitize_name(name)}-secret",
            namespace=namespace,
            labels=labels
        ),
        string_data=string_data,
        type="Opaque"
    )
    
    return core_v1_api.create_namespaced_secret(namespace=namespace, body=secret)


def get_configmap(
    core_v1_api: client.CoreV1Api,
    name: str,
    namespace: str = NAMESPACE
) -> Optional[Dict[str, Any]]:
    """
    Get resource spec from ConfigMap.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: ConfigMap name
        namespace: Kubernetes namespace
        
    Returns:
        Specification dictionary or None if not found
    """
    try:
        cm = core_v1_api.read_namespaced_config_map(
            name=sanitize_name(name),
            namespace=namespace
        )
        if cm.data and "spec.json" in cm.data:
            return json.loads(cm.data["spec.json"])
        return None
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return None
        raise


def get_secret(
    core_v1_api: client.CoreV1Api,
    name: str,
    namespace: str = NAMESPACE
) -> Dict[str, str]:
    """
    Get secret data from Secret.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: Resource name
        namespace: Kubernetes namespace
        
    Returns:
        Dictionary mapping field paths to values (empty if not found)
    """
    try:
        secret = core_v1_api.read_namespaced_secret(
            name=f"{sanitize_name(name)}-secret",
            namespace=namespace
        )
        if secret.data:
            import base64
            result = {}
            for k, v in secret.data.items():
                # Convert underscore back to dot notation
                path = k.replace('_', '.')
                result[path] = base64.b64decode(v).decode('utf-8')
            return result
        return {}
    except client.exceptions.ApiException as e:
        if e.status == 404:
            return {}
        raise


def merge_secret_data(spec: Dict[str, Any], secret_data: Dict[str, str]) -> Dict[str, Any]:
    """
    Merge secret data back into spec at proper paths.
    
    Args:
        spec: Public specification
        secret_data: Secret data mapping paths to values
        
    Returns:
        Complete specification with secrets merged
    """
    import copy
    complete_spec = copy.deepcopy(spec)
    
    for path, value in secret_data.items():
        set_value_at_path(complete_spec, path, value)
    
    return complete_spec


def update_configmap(
    core_v1_api: client.CoreV1Api,
    name: str,
    spec: Dict[str, Any],
    namespace: str = NAMESPACE
) -> client.V1ConfigMap:
    """
    Update ConfigMap with new spec.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: ConfigMap name
        spec: New specification dictionary
        namespace: Kubernetes namespace
        
    Returns:
        Updated ConfigMap object
    """
    cm = core_v1_api.read_namespaced_config_map(
        name=sanitize_name(name),
        namespace=namespace
    )
    
    cm.data = {"spec.json": json.dumps(spec, indent=2)}
    
    return core_v1_api.replace_namespaced_config_map(
        name=sanitize_name(name),
        namespace=namespace,
        body=cm
    )


def update_secret(
    core_v1_api: client.CoreV1Api,
    name: str,
    secret_data: Dict[str, str],
    namespace: str = NAMESPACE
) -> Optional[client.V1Secret]:
    """
    Update Secret with new data.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: Resource name
        secret_data: New secret data
        namespace: Kubernetes namespace
        
    Returns:
        Updated Secret object or None if no secret data
    """
    if not secret_data:
        # Delete secret if it exists but no longer needed
        try:
            core_v1_api.delete_namespaced_secret(
                name=f"{sanitize_name(name)}-secret",
                namespace=namespace
            )
        except client.exceptions.ApiException:
            pass
        return None
    
    try:
        secret = core_v1_api.read_namespaced_secret(
            name=f"{sanitize_name(name)}-secret",
            namespace=namespace
        )
        
        string_data = {k.replace('.', '_'): v for k, v in secret_data.items()}
        secret.string_data = string_data
        
        return core_v1_api.replace_namespaced_secret(
            name=f"{sanitize_name(name)}-secret",
            namespace=namespace,
            body=secret
        )
    except client.exceptions.ApiException as e:
        if e.status == 404:
            # Create if doesn't exist
            return create_secret(core_v1_api, name, "", secret_data, namespace)
        raise


def delete_configmap(
    core_v1_api: client.CoreV1Api,
    name: str,
    namespace: str = NAMESPACE
) -> None:
    """
    Delete ConfigMap.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: ConfigMap name
        namespace: Kubernetes namespace
    """
    try:
        core_v1_api.delete_namespaced_config_map(
            name=sanitize_name(name),
            namespace=namespace
        )
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise


def delete_secret(
    core_v1_api: client.CoreV1Api,
    name: str,
    namespace: str = NAMESPACE
) -> None:
    """
    Delete Secret.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        name: Resource name
        namespace: Kubernetes namespace
    """
    try:
        core_v1_api.delete_namespaced_secret(
            name=f"{sanitize_name(name)}-secret",
            namespace=namespace
        )
    except client.exceptions.ApiException as e:
        if e.status != 404:
            raise


def list_configmaps(
    core_v1_api: client.CoreV1Api,
    resource_group: str,
    namespace: str = NAMESPACE
) -> List[Dict[str, Any]]:
    """
    List all ConfigMaps for a resource group.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        resource_group: Resource group to filter by
        namespace: Kubernetes namespace
        
    Returns:
        List of specification dictionaries
    """
    label_selector = f"agentbox.io/resource-group={resource_group}"
    
    try:
        cm_list = core_v1_api.list_namespaced_config_map(
            namespace=namespace,
            label_selector=label_selector
        )
        
        results = []
        for cm in cm_list.items:
            if cm.data and "spec.json" in cm.data:
                spec = json.loads(cm.data["spec.json"])
                results.append(spec)
        
        return results
    except client.exceptions.ApiException:
        return []


def ensure_namespace(core_v1_api: client.CoreV1Api, namespace: str = NAMESPACE) -> None:
    """
    Ensure the agentbox-system namespace exists.
    
    Args:
        core_v1_api: Kubernetes CoreV1Api client
        namespace: Namespace name
    """
    try:
        core_v1_api.read_namespace(name=namespace)
    except client.exceptions.ApiException as e:
        if e.status == 404:
            ns = client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=namespace,
                    labels=COMMON_LABELS
                )
            )
            core_v1_api.create_namespace(body=ns)
        else:
            raise

