"""
Controller Manager
Watches every AgentBox kind and drives it towards its declared state.

One work queue, watchers per kind feeding it, and a periodic resync so nothing
is missed when a watch drops. Reconcilers are plain functions: given a resource,
return the status to write.
"""
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from kubernetes import client, watch

from controller.context import GROUP, VERSION, Context, logger
from controller.leader import LeaderElector
from controller.reconcilers import data, governance, scaling, workloads

Reconciler = Callable[[Context, Dict[str, Any]], Dict[str, Any]]

# CRD plural -> the function that reconciles it.
#
# Order matters within a pass. The kinds that decide something — what a metric
# reads, whether a budget is spent, whether a guardrail has tripped — run before
# the kinds that act on those decisions, so enforcement reaches a gateway in the
# same pass rather than the next one.
RECONCILERS: Dict[str, Reconciler] = {
    # decide
    "aimetrics": governance.reconcile_ai_metric,
    "aimeters": governance.reconcile_ai_meter,
    "guardrails": governance.reconcile_guardrail,
    "agentidps": governance.reconcile_agent_idp,
    "tracers": governance.reconcile_tracer,
    "datasets": data.reconcile_dataset,
    "recipes": data.reconcile_recipe,
    # act
    "models": workloads.reconcile_model,
    "gateways": workloads.reconcile_gateway,
    "toolservers": workloads.reconcile_tool_server,
    "harnessruntimes": workloads.reconcile_harness_runtime,
    "trainloops": workloads.reconcile_train_loop,
    "evaluators": workloads.reconcile_evaluator,
    # scale, once the targets above have reported
    "modelautoscalers": scaling.reconcile_autoscaler,
    "harnessswarmautoscalers": scaling.reconcile_autoscaler,
    "toolserverautoscalers": scaling.reconcile_autoscaler,
}


class Manager:
    """Runs the reconcile loop for every AgentBox kind."""

    def __init__(self, ctx: Context, resync_seconds: int = 60,
                 workers: int = 4, elector: Optional[LeaderElector] = None):
        """
        Args:
            ctx: Controller context
            resync_seconds: How often to re-reconcile everything
            workers: Number of reconcile threads
            elector: Leader elector; when set, only the holder reconciles
        """
        self.ctx = ctx
        self.resync_seconds = resync_seconds
        self.workers = workers
        self.elector = elector
        self.queue: "queue.Queue[Tuple[str, str, str]]" = queue.Queue()
        self.stop = threading.Event()
        self.reconciled = 0
        self.failures = 0

    # ------------------------------------------------------------------ sources
    def enqueue(self, plural: str, namespace: str, name: str) -> None:
        """Queue one resource for reconciliation."""
        self.queue.put((plural, namespace, name))

    def resync(self) -> int:
        """
        Enqueue every resource of every kind.

        Returns:
            Number of resources enqueued
        """
        total = 0
        for plural in RECONCILERS:
            try:
                for item in self.ctx.list_resources(plural):
                    meta = item["metadata"]
                    self.enqueue(plural, meta["namespace"], meta["name"])
                    total += 1
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.warning("CRD %s is not installed; skipping", plural)
                else:
                    logger.error("listing %s failed: %s", plural, e.reason)
        return total

    def _watch(self, plural: str) -> None:
        """Watch one kind and enqueue what changes."""
        watcher = watch.Watch()
        while not self.stop.is_set():
            try:
                if self.ctx.namespace:
                    stream = watcher.stream(
                        self.ctx.custom.list_namespaced_custom_object,
                        GROUP, VERSION, self.ctx.namespace, plural,
                        timeout_seconds=self.resync_seconds)
                else:
                    stream = watcher.stream(
                        self.ctx.custom.list_cluster_custom_object,
                        GROUP, VERSION, plural,
                        timeout_seconds=self.resync_seconds)
                for event in stream:
                    if self.stop.is_set():
                        break
                    if event["type"] == "DELETED":
                        continue  # children go with the owner reference
                    meta = event["object"]["metadata"]
                    self.enqueue(plural, meta["namespace"], meta["name"])
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.warning("CRD %s is not installed; watcher stopping", plural)
                    return
                logger.debug("watch on %s restarting: %s", plural, e.reason)
                time.sleep(2)
            except Exception as e:  # noqa: BLE001 - a watcher must never die quietly
                logger.debug("watch on %s restarting: %s", plural, e)
                time.sleep(2)

    # ---------------------------------------------------------------- reconcile
    def reconcile_one(self, plural: str, namespace: str, name: str) -> Optional[str]:
        """
        Reconcile a single resource and write its status.

        Args:
            plural: CRD plural
            namespace: Resource namespace
            name: Resource name

        Returns:
            The reported state, or None if the resource is gone
        """
        resource = self.ctx.get_resource(plural, namespace, name)
        if resource is None:
            return None
        if resource["metadata"].get("deletionTimestamp"):
            return None

        reconciler = RECONCILERS[plural]
        try:
            status_block = reconciler(self.ctx, resource)
        except client.exceptions.ApiException as e:
            self.failures += 1
            logger.error("%s/%s: %s", plural, name, e.reason)
            self.ctx.patch_status(plural, namespace, name, {
                "state": "failed",
                "message": f"{e.status} {e.reason}",
            })
            return "failed"
        except Exception as e:  # noqa: BLE001 - one bad resource must not stop the loop
            self.failures += 1
            logger.exception("%s/%s reconcile failed", plural, name)
            self.ctx.patch_status(plural, namespace, name, {
                "state": "failed",
                "message": f"{type(e).__name__}: {e}",
            })
            return "failed"

        self.ctx.patch_status(plural, namespace, name, status_block)
        self.reconciled += 1
        logger.info("%-24s %s/%s -> %s", plural, namespace, name, status_block.get("state"))
        return status_block.get("state")

    def _worker(self) -> None:
        """Pull from the queue until told to stop."""
        while not self.stop.is_set():
            try:
                plural, namespace, name = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                if self.elector is None or self.elector.is_leader.is_set():
                    self.reconcile_one(plural, namespace, name)
            finally:
                self.queue.task_done()

    # -------------------------------------------------------------------- lifecycle
    def run_once(self) -> Dict[str, int]:
        """
        Reconcile everything once and return. Used by tests and by `--once`.

        Returns:
            Counts of resources reconciled and failures
        """
        total = self.resync()
        while not self.queue.empty():
            plural, namespace, name = self.queue.get()
            self.reconcile_one(plural, namespace, name)
            self.queue.task_done()
        return {"resources": total, "reconciled": self.reconciled, "failures": self.failures}

    def run(self) -> None:
        """Run until interrupted: watchers, workers and a periodic resync."""
        logger.info("agentbox controller starting: %d kinds, %d workers, %ds resync",
                    len(RECONCILERS), self.workers, self.resync_seconds)

        threads = []

        if self.elector is not None:
            thread = threading.Thread(target=self.elector.run, daemon=True)
            thread.start()
            threads.append(thread)
            logger.info("waiting for leadership")
            while not self.elector.is_leader.is_set() and not self.stop.is_set():
                self.stop.wait(1)

        for _ in range(self.workers):
            thread = threading.Thread(target=self._worker, daemon=True)
            thread.start()
            threads.append(thread)

        for plural in RECONCILERS:
            thread = threading.Thread(target=self._watch, args=(plural,), daemon=True)
            thread.start()
            threads.append(thread)

        try:
            while not self.stop.is_set():
                if self.elector is not None and not self.elector.is_leader.is_set():
                    self.stop.wait(self.resync_seconds)
                    continue
                count = self.resync()
                logger.debug("resync enqueued %d resources", count)
                self.stop.wait(self.resync_seconds)
        except KeyboardInterrupt:
            logger.info("shutting down")
        finally:
            self.stop.set()
            if self.elector is not None:
                self.elector.release()
            for thread in threads:
                thread.join(timeout=2)
