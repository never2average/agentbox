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
    
    def _load_schema(self) -> Optional[Dict[str, Any]]:
        """
        Load JSON schema for this resource group.
        
        Returns:
            Schema dictionary or None if not found
        """
        schema_file = Path(__file__).parent.parent / "schemas" / f"{self.resource_group}-schema.json"
        
        if not schema_file.exists():
            return None
        
        with open(schema_file, 'r') as f:
            return json.load(f)
    
    def _validate_spec(self, spec: Dict[str, Any]) -> None:
        """
        Validate spec against JSON schema.
        
        Args:
            spec: Specification to validate
            
        Raises:
            jsonschema.ValidationError: If validation fails
        """
        if self._schema is None:
            return
        
        try:
            jsonschema.validate(instance=spec, schema=self._schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Validation failed for {self.resource_group}: {e.message}")
    
    def _extract_name(self, spec: Dict[str, Any]) -> str:
        """
        Extract resource name from spec using standard fields.
        
        Args:
            spec: Specification dictionary
            
        Returns:
            Extracted and sanitized name
            
        Raises:
            ValueError: If name cannot be determined
        """
        # Try common name fields in order of preference
        name_candidates = [
            spec.get('metadata', {}).get('runtime_id'),
            spec.get('metadata', {}).get('id'),
            spec.get('metadata', {}).get('name'),
            spec.get('id'),
            spec.get('name')
        ]
        
        for name in name_candidates:
            if name:
                return resource_store.sanitize_name(str(name))
        
        raise ValueError(f"Cannot determine name from spec for {self.resource_group}")
    
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
        # Validate spec
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
        
        # Validate
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

