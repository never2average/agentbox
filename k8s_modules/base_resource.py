"""
Base Resource Manager Module
Abstract base class for all resource managers with CRUD, validation, and watch
"""
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Literal
from kubernetes import client, watch
import jsonschema
from k8s_modules.connection import load_kubeconfig
from k8s_modules import resource_store


_SCHEMA_STORE: Dict[str, Dict[str, Any]] = {}


def _load_schema_store(schema_dir: Path) -> Dict[str, Dict[str, Any]]:
    """
    Load every CRD schema once, keyed by both its $id and its file URI.

    Schemas declare an absolute $id (https://agentbox.io/schemas/...), which
    makes relative cross-file $refs resolve to that host. Pre-seeding the
    resolver store keeps resolution local and offline.

    Args:
        schema_dir: Directory holding the CRD schemas

    Returns:
        Mapping of URI to schema document
    """
    if _SCHEMA_STORE:
        return _SCHEMA_STORE

    base_uri = schema_dir.as_uri() + "/"
    for schema_file in sorted(schema_dir.glob("*-schema.json")):
        with open(schema_file, 'r') as f:
            schema = json.load(f)

        _SCHEMA_STORE[base_uri + schema_file.name] = schema
        if '$id' in schema:
            _SCHEMA_STORE[schema['$id']] = schema
            # Relative refs are resolved against the declaring schema's $id
            _SCHEMA_STORE[schema['$id'].rsplit('/', 1)[0] + '/' + schema_file.name] = schema

    return _SCHEMA_STORE


class BaseResourceManager(ABC):
    """
    Abstract base class for managing AgentBox resources backed by Kubernetes.
    """
    
    def __init__(self, kubeconfig_path: str, namespace: str = resource_store.NAMESPACE):
        """
        Initialize the resource manager.
        
        Args:
            kubeconfig_path: Path to kubeconfig file
            namespace: Kubernetes namespace (default: agentbox-system)
        """
        self.kubeconfig_path = kubeconfig_path
        self.namespace = namespace
        load_kubeconfig(kubeconfig_path)
        
        self.core_v1_api = client.CoreV1Api()
        self.apps_v1_api = client.AppsV1Api()
        self.batch_v1_api = client.BatchV1Api()
        
        # Ensure namespace exists
        resource_store.ensure_namespace(self.core_v1_api, namespace)
        
        # Load and cache schema
        self._schema = self._load_schema()
    
    @property
    @abstractmethod
    def resource_group(self) -> str:
        """
        Return the resource group name (e.g., "runtimes", "agents").
        """
        pass
    
    @property
    def schema_dir(self) -> Path:
        """Directory holding the CRD schemas."""
        return Path(__file__).parent.parent / "schemas"

    def _load_schema(self) -> Optional[Dict[str, Any]]:
        """
        Load JSON schema for this resource group.

        Returns:
            Schema dictionary or None if not found
        """
        schema_file = self.schema_dir / f"{self.resource_group}-schema.json"

        if not schema_file.exists():
            return None

        with open(schema_file, 'r') as f:
            return json.load(f)

    @property
    def crd(self) -> Dict[str, Any]:
        """CRD coordinates (group/version/kind/plural) declared by the schema."""
        if self._schema is None:
            return {}
        return self._schema.get('x-agentbox-crd', {})

    @property
    def kind(self) -> Optional[str]:
        """CRD kind managed by this manager (e.g. "HarnessRuntime")."""
        return self.crd.get('kind')

    @property
    def api_version(self) -> Optional[str]:
        """CRD apiVersion managed by this manager (e.g. "ai.agentbox.io/v1alpha1")."""
        group = self.crd.get('group')
        version = self.crd.get('version')
        return f"{group}/{version}" if group and version else None

    def _normalize_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fill in the standard CRD envelope fields when they are omitted.

        Args:
            spec: Resource document

        Returns:
            Document with apiVersion and kind populated
        """
        if not self.kind:
            return spec

        normalized = dict(spec)
        normalized.setdefault('apiVersion', self.api_version)
        normalized.setdefault('kind', self.kind)
        return normalized

    def _validate_spec(self, spec: Dict[str, Any]) -> None:
        """
        Validate spec against JSON schema.

        Args:
            spec: Specification to validate

        Raises:
            ValueError: If validation fails
        """
        if self._schema is None:
            return

        # Cross-file $refs (common-schema.json#/...) resolve from the local
        # schema store rather than being fetched over the network
        resolver = jsonschema.RefResolver(
            base_uri=self.schema_dir.as_uri() + "/",
            referrer=self._schema,
            store=_load_schema_store(self.schema_dir)
        )

        try:
            jsonschema.validate(
                instance=spec,
                schema=self._schema,
                resolver=resolver
            )
        except jsonschema.ValidationError as e:
            raise ValueError(f"Validation failed for {self.resource_group}: {e.message}")

    def _extract_name(self, spec: Dict[str, Any]) -> str:
        """
        Extract resource name from the standard CRD envelope.

        Args:
            spec: Resource document

        Returns:
            Extracted and sanitized name

        Raises:
            ValueError: If name cannot be determined
        """
        name = spec.get('metadata', {}).get('name')

        if name:
            return resource_store.sanitize_name(str(name))

        raise ValueError(
            f"Cannot determine name for {self.resource_group}: metadata.name is required"
        )
    
    def create(self, spec: Dict[str, Any], *, secret_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Create a new resource.
        
        Args:
            spec: Resource specification
            secret_fields: Optional list of dot-notation paths to treat as secrets
            
        Returns:
            Complete resource dictionary with status
            
        Raises:
            ValueError: If validation fails or resource already exists
        """
        # Normalize the CRD envelope, then validate
        spec = self._normalize_spec(spec)
        self._validate_spec(spec)

        # Extract name
        name = self._extract_name(spec)
        
        # Check if already exists
        existing = resource_store.get_configmap(self.core_v1_api, name, self.namespace)
        if existing is not None:
            raise ValueError(f"Resource {name} already exists in {self.resource_group}")
        
        # Split public and secret data
        public_spec, secret_data = resource_store.extract_secret_fields(spec, secret_fields)
        
        # Create ConfigMap
        resource_store.create_configmap(
            self.core_v1_api,
            name,
            self.resource_group,
            public_spec,
            self.namespace
        )
        
        # Create Secret if needed
        if secret_data:
            resource_store.create_secret(
                self.core_v1_api,
                name,
                self.resource_group,
                secret_data,
                self.namespace
            )
        
        # Create any workloads (subclass responsibility)
        self._create_workloads(name, spec)
        
        # Return complete resource with status
        return self.get(name)
    
    def get(self, name: str, *, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get a resource by name.
        
        Args:
            name: Resource name
            include_secrets: Whether to include secret fields
            
        Returns:
            Resource dictionary with status or None if not found
        """
        name = resource_store.sanitize_name(name)
        
        # Get public spec from ConfigMap
        spec = resource_store.get_configmap(self.core_v1_api, name, self.namespace)
        if spec is None:
            return None
        
        # Merge secrets if requested
        if include_secrets:
            secret_data = resource_store.get_secret(self.core_v1_api, name, self.namespace)
            spec = resource_store.merge_secret_data(spec, secret_data)
        
        # Synthesize status from workloads
        status = self._synthesize_status(name, spec)
        
        result = spec.copy()
        if 'status' not in result:
            result['status'] = {}
        result['status'].update(status)
        
        return result
    
    def update(
        self,
        name: str,
        spec: Dict[str, Any],
        strategy: Literal["merge", "replace"] = "merge",
        *,
        secret_fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Update an existing resource.
        
        Args:
            name: Resource name
            spec: New or partial specification
            strategy: "merge" to merge with existing, "replace" to replace entirely
            secret_fields: Optional list of dot-notation paths to treat as secrets
            
        Returns:
            Updated resource dictionary with status
            
        Raises:
            ValueError: If validation fails or resource not found
        """
        name = resource_store.sanitize_name(name)
        
        # Get existing spec
        existing = resource_store.get_configmap(self.core_v1_api, name, self.namespace)
        if existing is None:
            raise ValueError(f"Resource {name} not found in {self.resource_group}")
        
        # Merge or replace
        if strategy == "merge":
            import copy
            merged_spec = copy.deepcopy(existing)
            self._deep_merge(merged_spec, spec)
            final_spec = merged_spec
        else:
            final_spec = spec

        # Normalize the CRD envelope, then validate
        final_spec = self._normalize_spec(final_spec)
        self._validate_spec(final_spec)
        
        # Split public and secret data
        public_spec, secret_data = resource_store.extract_secret_fields(final_spec, secret_fields)
        
        # Update ConfigMap
        resource_store.update_configmap(
            self.core_v1_api,
            name,
            public_spec,
            self.namespace
        )
        
        # Update Secret
        if secret_data or secret_fields:
            resource_store.update_secret(
                self.core_v1_api,
                name,
                secret_data,
                self.namespace
            )
        
        # Update workloads (subclass responsibility)
        self._update_workloads(name, final_spec)
        
        return self.get(name)
    
    def delete(self, name: str) -> None:
        """
        Delete a resource.
        
        Args:
            name: Resource name
            
        Raises:
            ValueError: If resource not found
        """
        name = resource_store.sanitize_name(name)
        
        # Check if exists
        existing = resource_store.get_configmap(self.core_v1_api, name, self.namespace)
        if existing is None:
            raise ValueError(f"Resource {name} not found in {self.resource_group}")
        
        # Delete workloads first (subclass responsibility)
        self._delete_workloads(name)
        
        # Delete Secret
        resource_store.delete_secret(self.core_v1_api, name, self.namespace)
        
        # Delete ConfigMap (cascade delete via ownerReferences)
        resource_store.delete_configmap(self.core_v1_api, name, self.namespace)
    
    def list(self, selector: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """
        List all resources in this group.
        
        Args:
            selector: Optional label selector dictionary
            
        Returns:
            List of resource dictionaries with status
        """
        specs = resource_store.list_configmaps(
            self.core_v1_api,
            self.resource_group,
            self.namespace
        )
        
        results = []
        for spec in specs:
            name = self._extract_name(spec)
            # Get full resource with status
            resource = self.get(name)
            if resource:
                results.append(resource)
        
        return results
    
    def watch(self, callback: Callable[[str, Dict[str, Any]], None], timeout_seconds: Optional[int] = None) -> None:
        """
        Watch for changes to resources in this group.
        
        Args:
            callback: Function to call with (event_type, resource) on changes
            timeout_seconds: Optional timeout
        """
        w = watch.Watch()
        label_selector = f"agentbox.io/resource-group={self.resource_group}"
        
        try:
            for event in w.stream(
                self.core_v1_api.list_namespaced_config_map,
                namespace=self.namespace,
                label_selector=label_selector,
                timeout_seconds=timeout_seconds
            ):
                event_type = event['type']  # ADDED, MODIFIED, DELETED
                cm = event['object']
                
                if cm.data and "spec.json" in cm.data:
                    spec = json.loads(cm.data["spec.json"])
                    name = self._extract_name(spec)
                    resource = self.get(name)
                    if resource:
                        callback(event_type, resource)
        except Exception as e:
            # Watch may timeout or be interrupted
            pass
    
    def _deep_merge(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        """
        Deep merge source into target dictionary.
        
        Args:
            target: Target dictionary (modified in place)
            source: Source dictionary
        """
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value
    
    # Abstract methods for subclasses to implement
    
    @abstractmethod
    def _create_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Create Kubernetes workloads for this resource.
        
        Args:
            name: Resource name
            spec: Full specification
        """
        pass
    
    @abstractmethod
    def _update_workloads(self, name: str, spec: Dict[str, Any]) -> None:
        """
        Update Kubernetes workloads for this resource.
        
        Args:
            name: Resource name
            spec: Updated specification
        """
        pass
    
    @abstractmethod
    def _delete_workloads(self, name: str) -> None:
        """
        Delete Kubernetes workloads for this resource.
        
        Args:
            name: Resource name
        """
        pass
    
    @abstractmethod
    def _synthesize_status(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synthesize status from Kubernetes workloads and spec.
        
        Args:
            name: Resource name
            spec: Specification
            
        Returns:
            Status dictionary
        """
        pass

