#!/usr/bin/env python3
"""
End-to-end test for the AgentBox CRDs against a live Kubernetes API server.

Covers the full loop:
  1. CRDs install and become Established
  2. Every reference example is accepted, defaulted and stored
  3. Bad specs are rejected — required fields, enums, unknown fields, CEL rules
  4. Printer columns render
  5. The scale subresource works on HarnessRuntime and ToolServer
  6. The status subresource round-trips without clobbering spec
  7. The Python managers CRUD every kind, and create real workloads

Usage:
    python tests/e2e_test.py --context <kube-context> [--namespace agentbox-e2e]
    python tests/e2e_test.py --context <ctx> --keep      # leave resources behind
    python tests/e2e_test.py --context <ctx> --no-install  # CRDs already installed

Everything is created in a dedicated namespace and deleted at the end. The CRDs
themselves are cluster-scoped: they are installed unless --no-install is passed,
and removed at the end unless --keep is.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
CRD_DIR = ROOT / "crds"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results = []


def record(name, status, detail=""):
    results.append((name, status, detail))
    mark = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
    print(f"[{mark}] {name}" + (f"\n         {detail}" if detail and status != PASS else ""))
    return status == PASS


KUBECONFIG = None


def kubectl(*args, context=None, input_text=None, check=True):
    """Run kubectl and return (returncode, stdout, stderr)."""
    cmd = ["kubectl"]
    if KUBECONFIG:
        cmd += ["--kubeconfig", KUBECONFIG]
    if context:
        cmd += ["--context", context]
    cmd += list(args)
    proc = subprocess.run(cmd, input=input_text, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.returncode, proc.stdout, proc.stderr


def apply(doc, context, namespace, check=True):
    """Apply a resource dict, returning (returncode, stdout, stderr)."""
    return kubectl("apply", "-n", namespace, "-f", "-",
                   context=context, input_text=json.dumps(doc), check=check)


def wait_gone(resource, name, context, namespace, timeout=60):
    """Poll until a resource is actually gone; deletion is asynchronous."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, _, _ = kubectl("get", resource, name, "-n", namespace,
                             context=context, check=False)
        if code != 0:
            return True
        time.sleep(2)
    return False


def load_examples():
    """Every reference example, keyed by kind."""
    examples = {}
    for f in sorted(SCHEMA_DIR.glob("*-schema.json")):
        doc = json.loads(f.read_text())
        crd = doc.get("x-agentbox-crd")
        if crd and doc.get("examples"):
            examples[crd["kind"]] = (crd, doc["examples"][0])
    return examples


# --------------------------------------------------------------------------- 1
def test_install(context, install):
    if not install:
        return record("CRDs already installed (--no-install)", SKIP)

    try:
        kubectl("apply", "-k", str(CRD_DIR), context=context)
    except RuntimeError as e:
        return record("CRDs apply", FAIL, str(e))
    record("CRDs apply", PASS)

    try:
        kubectl("wait", "--for=condition=Established", "--timeout=90s",
                "crd", "-l", "app.kubernetes.io/part-of=agentbox", context=context)
    except RuntimeError as e:
        return record("CRDs reach Established", FAIL, str(e))
    return record("CRDs reach Established", PASS)


def test_discovery(context, examples):
    _, out, _ = kubectl("api-resources", "--categories=agentbox", "-o", "name", context=context)
    found = {line.split(".")[0] for line in out.split()}
    expected = {crd["plural"] for crd, _ in examples.values()}
    missing = expected - found
    if missing:
        return record("all 16 kinds are discoverable", FAIL, f"missing: {sorted(missing)}")
    return record(f"all {len(expected)} kinds are discoverable", PASS)


# --------------------------------------------------------------------------- 2
def test_examples_accepted(context, namespace, examples):
    ok = True
    for kind, (crd, example) in sorted(examples.items()):
        code, _, err = apply(example, context, namespace, check=False)
        if code != 0:
            ok &= record(f"apply example {kind}", FAIL, err.strip().splitlines()[-1][:220])
            continue
        name = example["metadata"]["name"]
        code, out, err = kubectl("get", crd["plural"], name, "-n", namespace,
                                 "-o", "json", context=context, check=False)
        if code != 0:
            ok &= record(f"read back {kind}", FAIL, err.strip()[:200])
            continue
        stored = json.loads(out)
        if stored["apiVersion"] != "ai.agentbox.io/v1beta1":
            ok &= record(f"read back {kind}", FAIL, f"apiVersion {stored['apiVersion']}")
            continue
        ok &= record(f"apply + read back {kind}", PASS)
    return ok


def test_defaulting(context, namespace):
    """The API server should apply schema defaults."""
    _, out, _ = kubectl("get", "harnessruntimes", "api-harness", "-n", namespace,
                        "-o", "json", context=context)
    spec = json.loads(out)["spec"]
    endpoint = spec["endpoints"][0]
    checks = {
        "endpoints[0].interface defaulted to http": endpoint.get("interface") == "http",
        "health.periodSeconds defaulted to 30": spec.get("health", {}).get("periodSeconds") == 30,
    }
    ok = True
    for label, passed in checks.items():
        ok &= record(label, PASS if passed else FAIL, json.dumps(spec.get("health", {})))
    return ok


def test_pruning(context, namespace):
    """Unknown fields are rejected under strict validation, and pruned without it."""
    doc = {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Model",
        "metadata": {"name": "prune-probe"},
        "spec": {"modelName": "probe", "modelHub": "huggingface",
                 "hubModelId": "org/probe", "totallyMadeUpField": "should vanish"},
    }

    code, _, err = apply(doc, context, namespace, check=False)
    ok = record("unknown fields are rejected under strict validation",
                PASS if code != 0 else FAIL, "the API server accepted a typo")

    code, _, err = kubectl("apply", "--validate=ignore", "-n", namespace, "-f", "-",
                           context=context, input_text=json.dumps(doc), check=False)
    if code != 0:
        return ok & record("unknown fields are pruned", FAIL, err.strip()[:200])
    _, out, _ = kubectl("get", "models", "prune-probe", "-n", namespace,
                        "-o", "json", context=context)
    stored = json.loads(out)["spec"]
    return ok & record("unknown fields are pruned when validation is relaxed",
                       PASS if "totallyMadeUpField" not in stored else FAIL,
                       json.dumps(stored))


# --------------------------------------------------------------------------- 3
REJECTIONS = [
    ("missing required field", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
        "metadata": {"name": "bad-missing-code"},
        "spec": {"runtimeKind": "server"}}),
    ("invalid enum value", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
        "metadata": {"name": "bad-enum"},
        "spec": {"runtimeKind": "quantum", "code": {"image": "acme/x:1"},
                 "endpoints": [{"name": "api", "port": 8080}]}}),
    ("image pattern violation", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
        "metadata": {"name": "bad-image"},
        "spec": {"runtimeKind": "server", "code": {"image": "NOT A VALID IMAGE!!"},
                 "endpoints": [{"name": "api", "port": 8080}]}}),
    ("port out of range", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
        "metadata": {"name": "bad-port"},
        "spec": {"runtimeKind": "server", "code": {"image": "acme/x:1"},
                 "endpoints": [{"name": "api", "port": 99999}]}}),
    ("CEL: cron harness without a schedule", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
        "metadata": {"name": "bad-cron"},
        "spec": {"runtimeKind": "cron", "code": {"image": "acme/x:1"}}}),
    ("CEL: server harness without an endpoint", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
        "metadata": {"name": "bad-server"},
        "spec": {"runtimeKind": "server", "code": {"image": "acme/x:1"}}}),
    ("CEL: exec health check without a command", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
        "metadata": {"name": "bad-exec"},
        "spec": {"runtimeKind": "server", "code": {"image": "acme/x:1"},
                 "endpoints": [{"name": "api", "port": 8080}],
                 "health": {"type": "exec"}}}),
    ("CEL: autoscaler pointed at the wrong kind", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "ModelAutoScaler",
        "metadata": {"name": "bad-target"},
        "spec": {"scaleTargetRef": {"kind": "ToolServer", "name": "x"},
                 "bounds": {"maxReplicas": 4},
                 "metrics": [{"type": "resource", "resource": "gpu",
                              "target": {"metricType": "utilization", "value": 70}}]}}),
    ("CEL: maxReplicas below minReplicas", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "ModelAutoScaler",
        "metadata": {"name": "bad-bounds"},
        "spec": {"scaleTargetRef": {"kind": "Model", "name": "x"},
                 "bounds": {"minReplicas": 10, "maxReplicas": 2},
                 "metrics": [{"type": "resource", "resource": "gpu",
                              "target": {"metricType": "utilization", "value": 70}}]}}),
    ("CEL: scaling metric missing its metric name", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "ModelAutoScaler",
        "metadata": {"name": "bad-metric"},
        "spec": {"scaleTargetRef": {"kind": "Model", "name": "x"},
                 "bounds": {"maxReplicas": 4},
                 "metrics": [{"type": "aiMetric",
                              "target": {"metricType": "averageValue", "value": 5}}]}}),
    ("CEL: dataset config variant does not match type", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Dataset",
        "metadata": {"name": "bad-dataset"},
        "spec": {"name": "x", "type": "kafka", "direction": "source", "enabled": True,
                 "config": {"httpPoll": {"url": "https://x.example.com",
                                         "method": "GET", "intervalSeconds": 60}}}}),
    ("CEL: two dataset config variants set", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Dataset",
        "metadata": {"name": "bad-dataset-two"},
        "spec": {"name": "x", "type": "httpPoll", "direction": "source", "enabled": True,
                 "config": {"httpPoll": {"url": "https://x.example.com",
                                         "method": "GET", "intervalSeconds": 60},
                            "fs": {"path": "/tmp"}}}}),
    ("CEL: duplicate tool names in one server", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "ToolServer",
        "metadata": {"name": "bad-tools"},
        "spec": {"code": {"image": "acme/tools:1"}, "endpoint": {"port": 8080},
                 "tools": [
                     {"name": "dup", "parameters": {"type": "object"}, "returns": {"type": "object"}},
                     {"name": "dup", "parameters": {"type": "object"}, "returns": {"type": "object"}}]}}),
    ("CEL: scheduled train loop without a schedule", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "TrainLoop",
        "metadata": {"name": "bad-trainloop"},
        "spec": {"type": "training", "version": "1.0.0", "status": "active",
                 "worker": {"image": "acme/trainer:1"},
                 "execution": {"mode": "scheduled", "timeoutSeconds": 60}}}),
    ("CEL: billing period meter without a period", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "AIMeter",
        "metadata": {"name": "bad-meter"},
        "spec": {"usage": {"unit": "totalTokens", "source": {"metric": "gateway-tokens"}},
                 "window": {"type": "billingPeriod"}}}),
    ("CEL: exactly one evaluator dataset source", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Evaluator",
        "metadata": {"name": "bad-evaluator"},
        "spec": {"name": "x", "dataset": {}, "scoring": {"metrics": [
            {"metric": {"type": "contains", "needle": "x"}}]}}}),
]


def test_rejections(context, namespace):
    ok = True
    for label, doc in REJECTIONS:
        code, _, err = apply(doc, context, namespace, check=False)
        if code == 0:
            kubectl("delete", doc["kind"].lower() + "s", doc["metadata"]["name"],
                    "-n", namespace, context=context, check=False)
            ok &= record(f"rejects {label}", FAIL, "the API server accepted it")
        else:
            ok &= record(f"rejects {label}", PASS)
    return ok


# --------------------------------------------------------------------------- 4
def test_printer_columns(context, namespace):
    ok = True
    for plural, expected in [("harnessruntimes", ["KIND", "IMAGE", "REPLICAS", "READY", "STATE", "AGE"]),
                             ("toolservers", ["IMAGE", "PORT", "REPLICAS", "READY", "STATE", "AGE"]),
                             ("models", ["HUB", "HUBMODEL", "STATE", "AGE"]),
                             ("aimeters", ["UNIT", "WINDOW", "BUDGET", "STATE", "AGE"])]:
        _, out, _ = kubectl("get", plural, "-n", namespace, context=context)
        header = out.splitlines()[0].split() if out.strip() else []
        missing = [c for c in expected if c not in header]
        ok &= record(f"printer columns for {plural}",
                     PASS if not missing else FAIL,
                     f"header was {header}")
    return ok


# --------------------------------------------------------------------------- 5
def test_scale_subresource(context, namespace):
    ok = True
    examples = load_examples()
    targets = [("harnessruntimes", examples["HarnessRuntime"][1]["metadata"]["name"]),
               ("toolservers", examples["ToolServer"][1]["metadata"]["name"])]
    for plural, name in targets:
        code, _, err = kubectl("scale", f"{plural}/{name}", "--replicas=7",
                               "-n", namespace, context=context, check=False)
        if code != 0:
            ok &= record(f"kubectl scale {plural}/{name}", FAIL, err.strip()[:200])
            continue
        _, out, _ = kubectl("get", plural, name, "-n", namespace,
                            "-o", "jsonpath={.spec.replicas}", context=context)
        ok &= record(f"kubectl scale {plural}/{name}",
                     PASS if out.strip() == "7" else FAIL, f"spec.replicas={out.strip()}")
    return ok


def test_status_subresource(context, namespace):
    """Status writes must not touch spec, and spec writes must not touch status."""
    patch = json.dumps({"status": {"state": "active", "replicas": 7, "readyReplicas": 7,
                                   "selector": "agentbox.io/resource-name=api-harness"}})
    code, _, err = kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace,
                           "--subresource=status", "--type=merge", "-p", patch,
                           context=context, check=False)
    if code != 0:
        return record("status subresource accepts a write", FAIL, err.strip()[:200])
    record("status subresource accepts a write", PASS)

    _, out, _ = kubectl("get", "harnessruntimes", "api-harness", "-n", namespace,
                        "-o", "json", context=context)
    stored = json.loads(out)
    ok = record("status round-trips",
                PASS if stored.get("status", {}).get("state") == "active" else FAIL,
                json.dumps(stored.get("status", {})))

    # a spec-only apply must leave status intact
    code, _, _ = kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace,
                         "--type=merge", "-p", json.dumps({"spec": {"replicas": 3}}),
                         context=context, check=False)
    _, out, _ = kubectl("get", "harnessruntimes", "api-harness", "-n", namespace,
                        "-o", "json", context=context)
    after = json.loads(out)
    ok &= record("spec write leaves status intact",
                 PASS if after.get("status", {}).get("state") == "active" else FAIL,
                 json.dumps(after.get("status", {})))
    return ok


# --------------------------------------------------------------------------- 7
def test_python_managers(context, namespace, kubeconfig):
    """The CRUD layer, against the same live cluster."""
    sys.path.insert(0, str(ROOT))
    from k8s_modules.registry import CRD_KINDS, get_manager

    examples = load_examples()
    ok = True

    for group, kind in CRD_KINDS.items():
        crd, example = examples[kind]
        spec = json.loads(json.dumps(example))
        spec["metadata"]["name"] = spec["metadata"]["name"] + "-py"
        try:
            mgr = get_manager(group, kubeconfig, namespace)
            created = mgr.create(spec)
            assert created["metadata"]["name"] == spec["metadata"]["name"]
            assert created["kind"] == kind
            assert "status" in created

            fetched = mgr.get(spec["metadata"]["name"])
            assert fetched is not None, "get returned None"

            listed = mgr.list()
            assert any(r["metadata"]["name"] == spec["metadata"]["name"] for r in listed), \
                "created resource missing from list()"

            mgr.delete(spec["metadata"]["name"])
            assert mgr.get(spec["metadata"]["name"]) is None, "still present after delete"
            ok &= record(f"python CRUD {kind}", PASS)
        except Exception as e:  # noqa: BLE001 - report every kind, do not abort
            ok &= record(f"python CRUD {kind}", FAIL, f"{type(e).__name__}: {e}")
    return ok


def test_python_workloads(context, namespace, kubeconfig):
    """The three workload kinds must produce real Kubernetes objects."""
    sys.path.insert(0, str(ROOT))
    from k8s_modules.registry import get_manager

    ok = True

    cases = [
        ("harness-runtime", {
            "kind": "HarnessRuntime", "metadata": {"name": "e2e-harness"},
            "spec": {"runtimeKind": "server", "code": {"image": "busybox:1.36"},
                     "replicas": 1, "compute": {"cpu": {"cores": 1, "memoryMb": 128}},
                     "endpoints": [{"name": "api", "port": 8080}],
                     "env": {"E2E": "true"}}},
         [("deployment", "e2e-harness"), ("service", "e2e-harness")]),
        ("tool-server", {
            "kind": "ToolServer", "metadata": {"name": "e2e-tools"},
            "spec": {"code": {"image": "busybox:1.36"}, "endpoint": {"port": 8080},
                     "replicas": 1,
                     "tools": [{"name": "noop", "parameters": {"type": "object"},
                                "returns": {"type": "object"}}]}},
         [("deployment", "e2e-tools"), ("service", "e2e-tools")]),
        ("train-loop", {
            "kind": "TrainLoop", "metadata": {"name": "e2e-trainloop"},
            "spec": {"type": "training", "version": "1.0.0", "status": "active",
                     "worker": {"image": "busybox:1.36", "args": ["true"]},
                     "execution": {"mode": "scheduled", "timeoutSeconds": 60,
                                   "schedule": {"type": "cron",
                                                "cronExpression": "0 3 * * *"}}}},
         [("cronjob", "e2e-trainloop")]),
    ]

    for group, spec, expected in cases:
        try:
            mgr = get_manager(group, kubeconfig, namespace)
            mgr.create(spec)
        except Exception as e:  # noqa: BLE001
            ok &= record(f"python workload {spec['kind']}", FAIL, f"{type(e).__name__}: {e}")
            continue

        for resource, name in expected:
            code, _, err = kubectl("get", resource, name, "-n", namespace,
                                   context=context, check=False)
            ok &= record(f"{spec['kind']} creates {resource}/{name}",
                         PASS if code == 0 else FAIL, err.strip()[:160])

        status = mgr.get(spec["metadata"]["name"], include_secrets=False)["status"]
        ok &= record(f"{spec['kind']} synthesizes status",
                     PASS if status.get("state") else FAIL, json.dumps(status)[:160])

        mgr.delete(spec["metadata"]["name"])
        for resource, name in expected:
            ok &= record(f"{spec['kind']} deletes {resource}/{name}",
                         PASS if wait_gone(resource, name, context, namespace) else FAIL,
                         "workload still present after 60s")
    return ok


def test_python_secrets(context, namespace, kubeconfig):
    """Secret-looking fields must land in a Secret, not the ConfigMap."""
    sys.path.insert(0, str(ROOT))
    from k8s_modules.registry import get_manager

    name = "e2e-gateway"
    spec = {
        "kind": "Gateway", "metadata": {"name": name},
        "spec": {"modelName": "probe",
                 "litellmParams": {"model": "openai/probe",
                                   "apiBase": "http://example.svc/v1",
                                   "apiKey": "sk-super-secret-value"},
                 "modelInfo": {"id": "probe", "mode": "chat"}},
    }
    try:
        mgr = get_manager("gateway", kubeconfig, namespace)
        mgr.create(spec)
    except Exception as e:  # noqa: BLE001
        return record("secret extraction", FAIL, f"{type(e).__name__}: {e}")

    _, out, _ = kubectl("get", "configmap", name, "-n", namespace, "-o", "json", context=context)
    stored = out
    leaked = "sk-super-secret-value" in stored
    ok = record("secret value is absent from the ConfigMap", FAIL if leaked else PASS)

    code, out, _ = kubectl("get", "secret", f"{name}-secret", "-n", namespace,
                           "-o", "json", context=context, check=False)
    ok &= record("a Secret was created", PASS if code == 0 else FAIL)
    if code == 0:
        keys = list(json.loads(out).get("data", {}))
        ok &= record("the Secret holds the credential path",
                     PASS if any("apiKey" in k for k in keys) else FAIL, f"keys: {keys}")

    merged = mgr.get(name, include_secrets=True)
    ok &= record("include_secrets merges the value back",
                 PASS if merged["spec"]["litellmParams"].get("apiKey") == "sk-super-secret-value"
                 else FAIL)

    mgr.delete(name)
    return ok


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True, help="kube context to run against")
    parser.add_argument("--namespace", default="agentbox-e2e")
    parser.add_argument("--kubeconfig", default=os.environ.get("KUBECONFIG"),
                        help="kubeconfig the Python managers use; defaults to $KUBECONFIG")
    parser.add_argument("--no-install", action="store_true", help="assume CRDs are installed")
    parser.add_argument("--keep", action="store_true", help="do not clean up")
    args = parser.parse_args()

    if not args.kubeconfig:
        parser.error("--kubeconfig is required (or set KUBECONFIG); refusing to guess")

    print(f"context:    {args.context}")
    print(f"namespace:  {args.namespace}")
    print(f"kubeconfig: {args.kubeconfig}\n")

    global KUBECONFIG
    KUBECONFIG = args.kubeconfig

    examples = load_examples()

    if not test_install(args.context, not args.no_install):
        summarize()
        return 1

    kubectl("create", "namespace", args.namespace, context=args.context, check=False)

    stages = [
        ("discovery", test_discovery, (args.context, examples)),
        ("examples", test_examples_accepted, (args.context, args.namespace, examples)),
        ("defaulting", test_defaulting, (args.context, args.namespace)),
        ("pruning", test_pruning, (args.context, args.namespace)),
        ("rejections", test_rejections, (args.context, args.namespace)),
        ("printer columns", test_printer_columns, (args.context, args.namespace)),
        ("scale subresource", test_scale_subresource, (args.context, args.namespace)),
        ("status subresource", test_status_subresource, (args.context, args.namespace)),
        ("python CRUD", test_python_managers, (args.context, args.namespace, args.kubeconfig)),
        ("python workloads", test_python_workloads, (args.context, args.namespace, args.kubeconfig)),
        ("python secrets", test_python_secrets, (args.context, args.namespace, args.kubeconfig)),
    ]

    try:
        for label, fn, fn_args in stages:
            try:
                fn(*fn_args)
            except Exception as e:  # noqa: BLE001 - one broken stage must not hide the rest
                record(f"stage: {label}", FAIL, f"{type(e).__name__}: {e}")
    finally:
        if not args.keep:
            print("\ncleaning up...")
            kubectl("delete", "namespace", args.namespace, "--wait=false",
                    context=args.context, check=False)
            if not args.no_install:
                kubectl("delete", "-k", str(CRD_DIR), "--wait=false",
                        context=args.context, check=False)

    return summarize()


def summarize():
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = [(n, d) for n, s, d in results if s == FAIL]
    skipped = sum(1 for _, s, _ in results if s == SKIP)

    print("\n" + "=" * 72)
    print(f"{passed} passed, {len(failed)} failed, {skipped} skipped")
    print("=" * 72)
    for name, detail in failed:
        print(f"  FAIL  {name}")
        if detail:
            print(f"        {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
