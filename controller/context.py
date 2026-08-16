"""
Controller Context
Kubernetes clients, settings and logging shared by every reconciler.
"""
import logging
import os
from typing import Any, Dict, Optional

from kubernetes import client, config

GROUP = "ai.agentbox.io"
VERSION = "v1beta1"
API_VERSION = f"{GROUP}/{VERSION}"
FIELD_MANAGER = "agentbox-controller"

logger = logging.getLogger("agentbox")


def configure_logging(level: str = "INFO") -> None:
    """Set up structured-ish logging for the controller."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
        datefmt="%H:%M:%S"
    )


class Context:
    """
    Everything a reconciler needs: API clients, namespace scope and settings.
    """

    def __init__(self, kubeconfig: Optional[str] = None, namespace: Optional[str] = None,
                 prometheus_url: Optional[str] = None):
        """
        Initialize the controller context.

        Args:
            kubeconfig: Path to a kubeconfig; in-cluster config is used when absent
            namespace: Namespace to watch; all namespaces when absent
            prometheus_url: Prometheus base URL for metric lookups
        """
        if kubeconfig:
            config.load_kube_config(config_file=kubeconfig)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

        self.namespace = namespace
        self.prometheus_url = prometheus_url or os.environ.get("AGENTBOX_PROMETHEUS_URL")

        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.batch = client.BatchV1Api()
        self.rbac = client.RbacAuthorizationV1Api()
        self.custom = client.CustomObjectsApi()

    # ---------------------------------------------------------------- CR access
    def list_resources(self, plural: str) -> list:
        """
        List every instance of a CRD kind in scope.

        Args:
            plural: CRD plural, e.g. "harnessruntimes"

        Returns:
            List of resource dictionaries
        """
        if self.namespace:
            result = self.custom.list_namespaced_custom_object(
                GROUP, VERSION, self.namespace, plural)
        else:
            result = self.custom.list_cluster_custom_object(GROUP, VERSION, plural)
        return result.get("items", [])

    def get_resource(self, plural: str, namespace: str, name: str) -> Optional[Dict[str, Any]]:
        """Read one resource, or None if it is gone."""
        try:
            return self.custom.get_namespaced_custom_object(
                GROUP, VERSION, namespace, plural, name)
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return None
            raise

    def patch_status(self, plural: str, namespace: str, name: str,
                     status: Dict[str, Any]) -> None:
        """
        Write the status subresource.

        Args:
            plural: CRD plural
            namespace: Resource namespace
            name: Resource name
            status: Status object to merge
        """
        try:
            self.custom.patch_namespaced_custom_object_status(
                GROUP, VERSION, namespace, plural, name,
                {"status": status},
                field_manager=FIELD_MANAGER
            )
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    def patch_scale(self, plural: str, namespace: str, name: str, replicas: int) -> None:
        """
        Set spec.replicas on a scalable kind.

        Args:
            plural: CRD plural
            namespace: Resource namespace
            name: Resource name
            replicas: Desired replica count
        """
        self.custom.patch_namespaced_custom_object(
            GROUP, VERSION, namespace, plural, name,
            {"spec": {"replicas": replicas}},
            field_manager=FIELD_MANAGER
        )

    # ---------------------------------------------------------------- events
    def event(self, resource: Dict[str, Any], reason: str, message: str,
              kind: str = "Normal") -> None:
        """
        Emit a Kubernetes Event against a resource.

        Args:
            resource: The resource the event is about
            reason: Short CamelCase reason
            message: Human-readable message
            kind: "Normal" or "Warning"
        """
        meta = resource["metadata"]
        namespace = meta.get("namespace", "default")
        body = client.CoreV1Event(
            metadata=client.V1ObjectMeta(
                generate_name=f"{meta['name']}-",
                namespace=namespace
            ),
            involved_object=client.V1ObjectReference(
                api_version=API_VERSION,
                kind=resource["kind"],
                name=meta["name"],
                namespace=namespace,
                uid=meta.get("uid")
            ),
            reason=reason,
            message=message,
            type=kind,
            reporting_component=FIELD_MANAGER,
            reporting_instance=FIELD_MANAGER,
            action=reason,
            event_time=None
        )
        try:
            self.core.create_namespaced_event(namespace, body)
        except client.exceptions.ApiException:
            logger.debug("could not emit event %s for %s", reason, meta["name"])
