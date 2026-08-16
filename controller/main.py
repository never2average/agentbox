#!/usr/bin/env python3
"""
AgentBox Controller entrypoint.

    agentbox-controller                          # watch every namespace
    agentbox-controller --namespace agents       # watch one
    agentbox-controller --once                   # reconcile once and exit
"""
import argparse
import sys

from controller.context import Context, configure_logging, logger
from controller.manager import Manager


def main() -> int:
    """Parse arguments and run the controller."""
    parser = argparse.ArgumentParser(prog="agentbox-controller")
    parser.add_argument("--kubeconfig", help="path to a kubeconfig; in-cluster config by default")
    parser.add_argument("--namespace", help="namespace to watch; all namespaces by default")
    parser.add_argument("--prometheus-url", help="Prometheus base URL for metric lookups")
    parser.add_argument("--resync-seconds", type=int, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--once", action="store_true", help="reconcile everything once and exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(args.log_level)

    ctx = Context(kubeconfig=args.kubeconfig, namespace=args.namespace,
                  prometheus_url=args.prometheus_url)
    manager = Manager(ctx, resync_seconds=args.resync_seconds, workers=args.workers)

    if args.once:
        result = manager.run_once()
        logger.info("reconciled %(reconciled)d of %(resources)d resources, %(failures)d failures",
                    result)
        return 1 if result["failures"] else 0

    manager.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
