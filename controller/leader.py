"""
Leader Election
A Lease-based lock so more than one controller replica is safe to run.

Only the holder reconciles. A replica that cannot acquire the lease waits; one
that loses it stops reconciling immediately rather than fighting the new holder.
"""
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client

from controller.context import Context, logger

LEASE_NAME = "agentbox-controller"


class LeaderElector:
    """Acquires and renews a coordination.k8s.io Lease."""

    def __init__(self, ctx: Context, namespace: str = "agentbox-system",
                 identity: Optional[str] = None,
                 lease_seconds: int = 15, renew_seconds: int = 5):
        """
        Args:
            ctx: Controller context
            namespace: Namespace holding the Lease
            identity: This replica's identity; the hostname by default
            lease_seconds: How long a lease is valid without renewal
            renew_seconds: How often the holder renews
        """
        self.ctx = ctx
        self.namespace = namespace
        self.identity = identity or socket.gethostname()
        self.lease_seconds = lease_seconds
        self.renew_seconds = renew_seconds
        self.api = client.CoordinationV1Api()
        self.is_leader = threading.Event()
        self.stop = threading.Event()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _lease_body(self, transitions: int) -> client.V1Lease:
        return client.V1Lease(
            metadata=client.V1ObjectMeta(name=LEASE_NAME, namespace=self.namespace),
            spec=client.V1LeaseSpec(
                holder_identity=self.identity,
                lease_duration_seconds=self.lease_seconds,
                acquire_time=self._now(),
                renew_time=self._now(),
                lease_transitions=transitions
            )
        )

    def try_acquire(self) -> bool:
        """
        Take the lease if it is free or expired, or renew it if we hold it.

        Returns:
            True when this replica holds the lease
        """
        try:
            lease = self.api.read_namespaced_lease(LEASE_NAME, self.namespace)
        except client.exceptions.ApiException as e:
            if e.status != 404:
                logger.warning("could not read the lease: %s", e.reason)
                return False
            try:
                self.api.create_namespaced_lease(self.namespace, self._lease_body(0))
                return True
            except client.exceptions.ApiException:
                return False

        holder = lease.spec.holder_identity
        renewed = lease.spec.renew_time
        duration = lease.spec.lease_duration_seconds or self.lease_seconds
        expired = renewed is None or (self._now() - renewed).total_seconds() > duration

        if holder == self.identity:
            lease.spec.renew_time = self._now()
        elif expired:
            logger.info("lease held by %s expired; taking over", holder)
            lease.spec.holder_identity = self.identity
            lease.spec.acquire_time = self._now()
            lease.spec.renew_time = self._now()
            lease.spec.lease_transitions = (lease.spec.lease_transitions or 0) + 1
        else:
            return False

        try:
            self.api.replace_namespaced_lease(LEASE_NAME, self.namespace, lease)
            return True
        except client.exceptions.ApiException as e:
            if e.status == 409:  # another replica won the race
                return False
            raise

    def run(self) -> None:
        """Hold the lease for as long as this replica is running."""
        while not self.stop.is_set():
            acquired = self.try_acquire()
            if acquired and not self.is_leader.is_set():
                logger.info("acquired leadership as %s", self.identity)
                self.is_leader.set()
            elif not acquired and self.is_leader.is_set():
                logger.warning("lost leadership; pausing reconciliation")
                self.is_leader.clear()
            self.stop.wait(self.renew_seconds)

    def release(self) -> None:
        """Give up the lease on shutdown so a standby takes over promptly."""
        self.stop.set()
        if not self.is_leader.is_set():
            return
        try:
            lease = self.api.read_namespaced_lease(LEASE_NAME, self.namespace)
            if lease.spec.holder_identity == self.identity:
                lease.spec.renew_time = None
                self.api.replace_namespaced_lease(LEASE_NAME, self.namespace, lease)
        except client.exceptions.ApiException:
            pass
        self.is_leader.clear()
