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
        record("CRDs already installed (--no-install)", SKIP)
        return True

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
    """
    Apply every reference example. Compute is scaled down first: the examples
    are sized for a real cluster and this often runs on a single kind node.
    """
    ok = True
    for kind, (crd, example) in sorted(examples.items()):
        compute = example.get("spec", {}).get("compute")
        if isinstance(compute, dict) and "cpu" in compute:
            compute["cpu"] = {"cores": 0.05, "memoryMb": 64}
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
    ("CEL: Gateway serving without an image", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Gateway",
        "metadata": {"name": "bad-gateway"},
        "spec": {"modelName": "x", "litellmParams": {"model": "openai/x", "apiBase": "http://x"},
                 "modelInfo": {"id": "x"}, "serving": {"port": 4000}}}),
    ("Model with an invalid hub", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Model",
        "metadata": {"name": "bad-hub"},
        "spec": {"modelName": "x", "modelHub": "napster", "hubModelId": "org/x"}}),
    ("Model serving image pattern violation", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Model",
        "metadata": {"name": "bad-serving-image"},
        "spec": {"modelName": "x", "modelHub": "huggingface", "hubModelId": "org/x",
                 "serving": {"image": "NOT VALID!!"}}}),
    ("AgentIdP without a status", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "AgentIdP",
        "metadata": {"name": "bad-idp"},
        "spec": {"identity": [{"type": "role", "roles": ["admin"]}]}}),
    ("AIMetric base type without its required fields", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "AIMetric",
        "metadata": {"name": "bad-metric-base"},
        "spec": {"type": "base", "metricName": "x"}}),
    ("Guardrail with an unknown effect", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Guardrail",
        "metadata": {"name": "bad-guardrail"},
        "spec": {"name": "x", "status": "enforce", "priority": 1,
                 "conditions": {"all": [{"metric": "m", "operator": "gt", "threshold": 1,
                                         "statistic": "Average", "periodSeconds": 60}]},
                 "effect": {"type": "detonate"}}}),
    ("Tracer log record with an invalid severity", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Tracer",
        "metadata": {"name": "bad-tracer"},
        "spec": {"resource": {"attributes": []},
                 "logRecords": [{"timeUnixNano": 1, "severityNumber": 99,
                                 "severityText": "SHOUTING",
                                 "body": {"type": "string", "stringValue": "x"}}]}}),
    ("Recipe without stages", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Recipe",
        "metadata": {"name": "bad-recipe"},
        "spec": {"coreMetadata": {"description": "x", "type": "workflow", "version": "1.0.0",
                                  "status": "active"},
                 "executionDefinition": {"stages": []}}}),
    ("HarnessSwarmAutoScaler pointed at a Model", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessSwarmAutoScaler",
        "metadata": {"name": "bad-swarm-target"},
        "spec": {"scaleTargetRef": {"kind": "Model", "name": "x"},
                 "bounds": {"maxReplicas": 4},
                 "metrics": [{"type": "aiMetric", "metric": "m",
                              "target": {"metricType": "averageValue", "value": 5}}]}}),
    ("ToolServerAutoScaler pointed at a HarnessRuntime", {
        "apiVersion": "ai.agentbox.io/v1beta1", "kind": "ToolServerAutoScaler",
        "metadata": {"name": "bad-ts-target"},
        "spec": {"scaleTargetRef": {"kind": "HarnessRuntime", "name": "x"},
                 "bounds": {"maxReplicas": 4},
                 "metrics": [{"type": "aiMetric", "metric": "m",
                              "target": {"metricType": "averageValue", "value": 5}}]}}),
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
                     "replicas": 1, "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}},
                     "endpoints": [{"name": "api", "port": 8080}],
                     "env": {"E2E": "true"}}},
         [("deployment", "e2e-harness"), ("service", "e2e-harness")]),
        ("tool-server", {
            "kind": "ToolServer", "metadata": {"name": "e2e-tools"},
            "spec": {"code": {"image": "busybox:1.36"}, "endpoint": {"port": 8080},
                     "replicas": 1, "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}},
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




# --------------------------------------------------------------------------- 8
def _run_controller(kubeconfig, namespace):
    """Reconcile everything once, in-process."""
    sys.path.insert(0, str(ROOT))
    from controller.context import Context
    from controller.manager import Manager

    manager = Manager(Context(kubeconfig=kubeconfig, namespace=namespace))
    return manager.run_once()


def _pin_metrics(context, namespace, values):
    """Pin metric values the controller will read."""
    body = {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "agentbox-metrics", "namespace": namespace},
        "data": {k: str(v) for k, v in values.items()},
    }
    kubectl("apply", "-n", namespace, "-f", "-", context=context,
            input_text=json.dumps(body))


def _spec_replicas(context, namespace, plural, name):
    _, out, _ = kubectl("get", plural, name, "-n", namespace,
                        "-o", "jsonpath={.spec.replicas}", context=context)
    return int(out.strip() or 0)


def _status_of(context, namespace, plural, name):
    _, out, _ = kubectl("get", plural, name, "-n", namespace, "-o", "json", context=context)
    return json.loads(out).get("status", {})


def test_controller_reconciles(context, namespace, kubeconfig):
    """Every kind reconciles, and the workload kinds build real objects."""
    result = _run_controller(kubeconfig, namespace)
    ok = record(f"controller reconciles all kinds ({result['reconciled']}/{result['resources']})",
                PASS if result["failures"] == 0 and result["reconciled"] == result["resources"]
                else FAIL, json.dumps(result))

    for resource, name, owner in [
        ("deployment", "api-harness", "HarnessRuntime"),
        ("service", "api-harness", "HarnessRuntime"),
        ("deployment", "summarize-text", "ToolServer"),
        ("service", "summarize-text", "ToolServer"),
        ("cronjob", "sft-nightly", "TrainLoop"),
        ("configmap", "summarize-text-tools", "ToolServer"),
        ("configmap", "openai-compatible-config", "Gateway"),
        ("configmap", "orders-stream-connector", "Dataset"),
        ("configmap", "otel-default-otel", "Tracer"),
        ("configmap", "request-latency-rule", "AIMetric"),
        ("configmap", "nightly-sft-plan", "Recipe"),
        ("configmap", "summarization-accuracy-suite", "Evaluator"),
        ("serviceaccount", "default-idp", "AgentIdP"),
        ("role", "default-idp", "AgentIdP"),
        ("rolebinding", "default-idp", "AgentIdP"),
    ]:
        code, out, err = kubectl("get", resource, name, "-n", namespace, "-o", "json",
                                 context=context, check=False)
        if code != 0:
            ok &= record(f"controller creates {resource}/{name}", FAIL, err.strip()[:140])
            continue
        refs = json.loads(out)["metadata"].get("ownerReferences", [])
        owned = any(r["kind"] == owner and r.get("controller") for r in refs)
        ok &= record(f"controller creates {resource}/{name} owned by {owner}",
                     PASS if owned else FAIL, f"ownerReferences: {refs}")

    # the tool catalog is how an agent discovers a tool server
    _, out, _ = kubectl("get", "configmap", "summarize-text-tools", "-n", namespace,
                        "-o", "jsonpath={.data.catalog\\.json}", context=context)
    catalog = json.loads(out)
    tool = catalog["tools"][0]
    ok &= record("tool catalog resolves a callable URL",
                 PASS if tool["url"].startswith(f"http://summarize-text.{namespace}.svc:8080")
                 else FAIL, tool["url"])
    return ok


def test_controller_status(context, namespace, kubeconfig):
    """Status carries state, conditions and the fields each kind promises."""
    ok = True
    expectations = [
        ("harnessruntimes", "api-harness", ["replicas", "readyReplicas", "selector", "endpoints"]),
        ("toolservers", "summarize-text", ["tools", "catalogConfigMap", "address"]),
        ("gateways", "openai-compatible", ["configMap", "upstream"]),
        ("datasets", "orders-stream", ["connectorConfigMap", "address"]),
        ("recipes", "nightly-sft", ["resolvedOrder", "stageCount"]),
        ("agentidps", "default-idp", ["serviceAccount", "role"]),
        ("tracers", "otel-default", ["collectorConfigMap"]),
        ("aimetrics", "request-latency", ["ruleConfigMap", "metricName"]),
        ("evaluators", "summarization-accuracy", ["suiteConfigMap", "caseCount"]),
    ]
    for plural, name, fields in expectations:
        status_block = _status_of(context, namespace, plural, name)
        missing = [f for f in fields if f not in status_block]
        has_conditions = bool(status_block.get("conditions"))
        ok &= record(f"status of {plural}/{name}",
                     PASS if not missing and has_conditions else FAIL,
                     f"missing {missing}, conditions={has_conditions}")

    order = _status_of(context, namespace, "recipes", "nightly-sft")["resolvedOrder"]
    ok &= record("recipe resolves its stage order",
                 PASS if order == ["ingest", "train"] else FAIL, str(order))
    return ok


def test_controller_scaling(context, namespace, kubeconfig):
    """Autoscalers move their targets by the HPA formula, and hold inside tolerance."""
    ok = True

    # api-harness sits at 2 replicas; 25 sessions per replica against a target of
    # 5 is 5x over, so the swarm should land on 10.
    kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 2}}), context=context)
    kubectl("patch", "models", "llama-3-70b-instruct", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 1}}), context=context)
    _pin_metrics(context, namespace, {
        "pending-agent-sessions": 25,
        "inference-queue-depth": 60,
        "tool-call-rate": 200,
        "gateway-tokens": 2_000_000_000,
        "request-rate": 1500,
    })
    _run_controller(kubeconfig, namespace)

    ok &= record("swarm autoscaler scales the harness 2 -> 10",
                 PASS if _spec_replicas(context, namespace, "harnessruntimes", "api-harness") == 10
                 else FAIL, str(_spec_replicas(context, namespace, "harnessruntimes", "api-harness")))
    ok &= record("model autoscaler scales the model 1 -> 3",
                 PASS if _spec_replicas(context, namespace, "models", "llama-3-70b-instruct") == 3
                 else FAIL, str(_spec_replicas(context, namespace, "models", "llama-3-70b-instruct")))

    # inside the tolerance band nothing moves
    kubectl("patch", "models", "llama-3-70b-instruct", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 3}}), context=context)
    _pin_metrics(context, namespace, {"inference-queue-depth": 21, "gateway-tokens": 1})
    _run_controller(kubeconfig, namespace)
    ok &= record("autoscaler holds inside the tolerance band",
                 PASS if _spec_replicas(context, namespace, "models", "llama-3-70b-instruct") == 3
                 else FAIL, str(_spec_replicas(context, namespace, "models", "llama-3-70b-instruct")))

    autoscaler = _status_of(context, namespace, "modelautoscalers", "llama-3-70b-autoscaler")
    ok &= record("autoscaler reports its observations",
                 PASS if autoscaler.get("metrics") and "currentReplicas" in autoscaler else FAIL,
                 json.dumps(autoscaler)[:180])

    # the scale reaches the Deployment
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "deployment", "api-harness", "-n", namespace,
                        "-o", "jsonpath={.spec.replicas}", context=context)
    ok &= record("the scaled replica count reaches the Deployment",
                 PASS if out.strip() == "10" else FAIL, f"deployment replicas={out.strip()}")
    return ok


def test_controller_metering_and_guardrails(context, namespace, kubeconfig):
    """Meters price usage against a budget; guardrails trip on their conditions."""
    ok = True
    _pin_metrics(context, namespace, {"gateway-tokens": 2_000_000_000, "request-rate": 1500})
    _run_controller(kubeconfig, namespace)

    meter = _status_of(context, namespace, "aimeters", "tenant-token-spend")
    ok &= record("meter prices usage (2e9 tokens -> 1200 USD)",
                 PASS if meter.get("currentCost") == 1200 else FAIL, json.dumps(meter)[:180])
    ok &= record("meter reports budget headroom (24%)",
                 PASS if meter.get("budgetUsedPercent") == 24 and not meter.get("budgetExceeded")
                 else FAIL, json.dumps(meter)[:180])

    guardrail = _status_of(context, namespace, "guardrails", "throttle-high-rps")
    ok &= record("guardrail trips when its condition is met",
                 PASS if guardrail.get("triggered") is True else FAIL, json.dumps(guardrail)[:180])

    _pin_metrics(context, namespace, {"gateway-tokens": 10_000_000_000, "request-rate": 10})
    _run_controller(kubeconfig, namespace)

    meter = _status_of(context, namespace, "aimeters", "tenant-token-spend")
    ok &= record("meter flags a breached budget",
                 PASS if meter.get("budgetExceeded") is True else FAIL, json.dumps(meter)[:180])

    guardrail = _status_of(context, namespace, "guardrails", "throttle-high-rps")
    ok &= record("guardrail clears when the condition passes",
                 PASS if guardrail.get("triggered") is False else FAIL, json.dumps(guardrail)[:180])

    _, out, _ = kubectl("get", "events", "-n", namespace, "-o", "json", context=context)
    reasons = {e["reason"] for e in json.loads(out)["items"]}
    for reason in ("Scaled", "BudgetExceeded", "GuardrailTripped"):
        ok &= record(f"controller emits the {reason} event",
                     PASS if reason in reasons else FAIL, f"saw {sorted(reasons)}")
    return ok


def test_controller_idempotent(context, namespace, kubeconfig):
    """A second pass over unchanged resources must not fail or thrash."""
    first = _run_controller(kubeconfig, namespace)
    second = _run_controller(kubeconfig, namespace)
    return record("reconciling twice is stable",
                  PASS if first["failures"] == 0 and second["failures"] == 0 else FAIL,
                  f"{first} then {second}")



def test_controller_serving_paths(context, namespace, kubeconfig):
    """Model and Gateway run in-cluster when spec.serving is set."""
    ok = True
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Model",
           "metadata": {"name": "served-model"},
           "spec": {"modelName": "served", "modelHub": "huggingface",
                    "hubModelId": "org/served", "replicas": 1,
                    "serving": {"image": "busybox:1.36", "port": 8000,
                                "nodeSelector": {"agentbox.io/gpu": "true"},
                                "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}}}}},
          context, namespace)
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Gateway",
           "metadata": {"name": "served-gateway"},
           "spec": {"modelName": "served", "replicas": 1,
                    "litellmParams": {"model": "openai/served", "apiBase": "http://x/v1"},
                    "modelInfo": {"id": "served", "mode": "chat"},
                    "serving": {"image": "busybox:1.36", "port": 4000,
                                "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}}}}},
          context, namespace)
    _run_controller(kubeconfig, namespace)

    for resource, name, owner in [("deployment", "served-model", "Model"),
                                  ("service", "served-model", "Model"),
                                  ("deployment", "served-gateway", "Gateway"),
                                  ("service", "served-gateway", "Gateway")]:
        code, out, err = kubectl("get", resource, name, "-n", namespace, "-o", "json",
                                 context=context, check=False)
        owned = code == 0 and any(r["kind"] == owner
                                  for r in json.loads(out)["metadata"].get("ownerReferences", []))
        ok &= record(f"serving {resource}/{name} owned by {owner}",
                     PASS if owned else FAIL, err.strip()[:140])

    _, out, _ = kubectl("get", "deployment", "served-model", "-n", namespace,
                        "-o", "jsonpath={.spec.template.spec.nodeSelector}", context=context)
    ok &= record("Model serving honours nodeSelector for GPU placement",
                 PASS if "agentbox.io/gpu" in out else FAIL, out)

    address = _status_of(context, namespace, "models", "served-model").get("address")
    ok &= record("Model reports its serving address",
                 PASS if address == f"served-model.{namespace}.svc:8000" else FAIL, str(address))

    registry = _status_of(context, namespace, "models", "llama-3-70b-instruct")
    ok &= record("a Model without serving stays a registry entry",
                 PASS if registry.get("state") == "active" and "address" not in registry
                 else FAIL, json.dumps(registry)[:160])
    return ok


def test_controller_workload_shapes(context, namespace, kubeconfig):
    """runtimeKind and execution.schedule pick the right workload."""
    ok = True
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
           "metadata": {"name": "batch-harness"},
           "spec": {"runtimeKind": "batch", "code": {"image": "busybox:1.36", "args": ["true"]},
                    "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}}}},
          context, namespace)
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
           "metadata": {"name": "cron-harness"},
           "spec": {"runtimeKind": "cron", "code": {"image": "busybox:1.36"},
                    "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}},
                    "schedule": {"cronExpression": "0 4 * * *", "timezone": "UTC"}}},
          context, namespace)
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "TrainLoop",
           "metadata": {"name": "oneshot-loop"},
           "spec": {"type": "training", "version": "1.0.0", "status": "active",
                    "worker": {"image": "busybox:1.36", "args": ["true"],
                               "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}}},
                    "execution": {"mode": "continuous", "timeoutSeconds": 60}}},
          context, namespace)
    _run_controller(kubeconfig, namespace)

    for resource, name, label in [("job", "batch-harness", "batch harness -> Job"),
                                  ("cronjob", "cron-harness", "cron harness -> CronJob"),
                                  ("job", "oneshot-loop", "unscheduled TrainLoop -> Job")]:
        code, _, err = kubectl("get", resource, name, "-n", namespace,
                               context=context, check=False)
        ok &= record(label, PASS if code == 0 else FAIL, err.strip()[:140])

    _, out, _ = kubectl("get", "cronjob", "cron-harness", "-n", namespace,
                        "-o", "jsonpath={.spec.schedule}", context=context)
    ok &= record("cron harness carries the declared schedule",
                 PASS if out.strip() == "0 4 * * *" else FAIL, out)

    code, _, _ = kubectl("get", "deployment", "batch-harness", "-n", namespace,
                         context=context, check=False)
    ok &= record("a batch harness gets no Deployment", PASS if code != 0 else FAIL)
    return ok


def test_controller_evaluator_run(context, namespace, kubeconfig):
    """Annotating an Evaluator with a run id starts exactly one Job."""
    ok = True
    kubectl("patch", "evaluators", "summarization-accuracy", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"runConfig": {"image": "busybox:1.36"}}}), context=context)
    kubectl("annotate", "evaluators", "summarization-accuracy", "-n", namespace,
            "agentbox.io/run=run7", "--overwrite", context=context)
    _run_controller(kubeconfig, namespace)

    code, _, err = kubectl("get", "job", "summarization-accuracy-run7", "-n", namespace,
                           context=context, check=False)
    ok &= record("Evaluator run annotation starts a Job",
                 PASS if code == 0 else FAIL, err.strip()[:140])

    evaluator = _status_of(context, namespace, "evaluators", "summarization-accuracy")
    ok &= record("Evaluator records the run id",
                 PASS if evaluator.get("lastRunId") == "run7" else FAIL,
                 json.dumps(evaluator)[:160])

    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "jobs", "-n", namespace, "-l",
                        "agentbox.io/resource-group=evaluator", "-o", "json", context=context)
    count = len(json.loads(out)["items"])
    ok &= record("the same run id does not start a second Job",
                 PASS if count == 1 else FAIL, f"{count} jobs")
    return ok


def test_controller_scaling_edges(context, namespace, kubeconfig):
    """Scale-to-zero, stabilization windows and the third autoscaler."""
    ok = True

    # ToolServerAutoScaler: 200 calls/replica against a target of 50 is 4x
    kubectl("patch", "toolservers", "summarize-text", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 2}}), context=context)
    _pin_metrics(context, namespace, {"tool-call-rate": 200})
    _run_controller(kubeconfig, namespace)
    ok &= record("tool server autoscaler scales 2 -> 8",
                 PASS if _spec_replicas(context, namespace, "toolservers", "summarize-text") == 8
                 else FAIL, str(_spec_replicas(context, namespace, "toolservers", "summarize-text")))

    # scale-to-zero: the swarm autoscaler allows a floor of 0
    kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 4}}), context=context)
    kubectl("patch", "harnessswarmautoscalers", "api-harness-swarm", "-n", namespace,
            "--type=merge", "-p", json.dumps({"status": {"lastScaleTime": None}}),
            "--subresource=status", context=context, check=False)
    _pin_metrics(context, namespace, {"pending-agent-sessions": 0})
    _run_controller(kubeconfig, namespace)
    replicas = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    ok &= record("scale-to-zero takes the harness to 0",
                 PASS if replicas == 0 else FAIL, f"replicas={replicas}")

    # demand returns, but the scale-up stabilization window still applies at zero
    _pin_metrics(context, namespace, {"pending-agent-sessions": 3})
    _run_controller(kubeconfig, namespace)
    held = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    swarm = _status_of(context, namespace, "harnessswarmautoscalers", "api-harness-swarm")
    ok &= record("the stabilization window also holds a scale up from zero",
                 PASS if held == 0 and "stabilization" in swarm.get("message", "")
                 else FAIL, f"replicas={held}: {swarm.get('message')}")

    # once the window passes, demand wakes it
    kubectl("patch", "harnessswarmautoscalers", "api-harness-swarm", "-n", namespace,
            "--type=merge", "-p", json.dumps({"spec": {"behavior": {
                "scaleUp": {"stabilizationWindowSeconds": 0}}}}), context=context)
    _run_controller(kubeconfig, namespace)
    woken = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    ok &= record("demand at zero replicas wakes the harness back up",
                 PASS if woken == 1 else FAIL, f"replicas={woken}")

    # a stabilization window blocks the next move
    kubectl("patch", "modelautoscalers", "llama-3-70b-autoscaler", "-n", namespace,
            "--type=merge", "-p", json.dumps({"spec": {"behavior": {
                "scaleUp": {"stabilizationWindowSeconds": 3600}}}}), context=context)
    kubectl("patch", "models", "llama-3-70b-instruct", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 1}}), context=context)
    _pin_metrics(context, namespace, {"inference-queue-depth": 200})
    _run_controller(kubeconfig, namespace)   # scales, stamping lastScaleTime
    before = _spec_replicas(context, namespace, "models", "llama-3-70b-instruct")
    kubectl("patch", "models", "llama-3-70b-instruct", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 1}}), context=context)
    _run_controller(kubeconfig, namespace)   # blocked by the window
    after = _spec_replicas(context, namespace, "models", "llama-3-70b-instruct")
    autoscaler = _status_of(context, namespace, "modelautoscalers", "llama-3-70b-autoscaler")
    ok &= record("stabilization window blocks the next scale",
                 PASS if after == 1 and "stabilization" in autoscaler.get("message", "")
                 else FAIL, f"{before} -> {after}: {autoscaler.get('message')}")

    ok &= record("autoscaler reports a missing target",
                 PASS if _autoscaler_missing_target(context, namespace, kubeconfig) else FAIL)
    return ok


def _autoscaler_missing_target(context, namespace, kubeconfig):
    """An autoscaler whose target does not exist reports degraded, not crashed."""
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "ToolServerAutoScaler",
           "metadata": {"name": "orphan-autoscaler"},
           "spec": {"scaleTargetRef": {"kind": "ToolServer", "name": "does-not-exist"},
                    "bounds": {"maxReplicas": 4},
                    "metrics": [{"type": "aiMetric", "metric": "tool-call-rate",
                                 "target": {"metricType": "averageValue", "value": 50}}]}},
          context, namespace)
    _run_controller(kubeconfig, namespace)
    result = _status_of(context, namespace, "toolserverautoscalers", "orphan-autoscaler")
    return result.get("state") == "degraded"


def test_controller_suspended_paths(context, namespace, kubeconfig):
    """enabled:false, paused and disabled are honoured, not ignored."""
    ok = True
    kubectl("patch", "toolservers", "summarize-text", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"enabled": False}}), context=context)
    kubectl("patch", "trainloops", "sft-nightly", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"status": "paused"}}), context=context)
    kubectl("patch", "modelautoscalers", "llama-3-70b-autoscaler", "-n", namespace,
            "--type=merge", "-p", json.dumps({"spec": {"enabled": False}}), context=context)
    kubectl("patch", "aimetrics", "request-latency", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"status": "inactive"}}), context=context)
    kubectl("patch", "guardrails", "throttle-high-rps", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"status": "disabled"}}), context=context)
    _run_controller(kubeconfig, namespace)

    for plural, name, expected in [("toolservers", "summarize-text", "suspended"),
                                   ("trainloops", "sft-nightly", "suspended"),
                                   ("modelautoscalers", "llama-3-70b-autoscaler", "suspended"),
                                   ("aimetrics", "request-latency", "inactive"),
                                   ("guardrails", "throttle-high-rps", "inactive")]:
        state = _status_of(context, namespace, plural, name).get("state")
        ok &= record(f"{plural}/{name} reports {expected} when switched off",
                     PASS if state == expected else FAIL, f"state={state}")

    ok &= record("a disabled tool server has its Deployment removed",
                 PASS if wait_gone("deployment", "summarize-text", context, namespace, 30)
                 else FAIL)

    # put it back
    kubectl("patch", "toolservers", "summarize-text", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"enabled": True}}), context=context)
    kubectl("patch", "guardrails", "throttle-high-rps", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"status": "enforce"}}), context=context)
    return ok


def test_controller_validation_paths(context, namespace, kubeconfig):
    """Recipe graphs and AgentIdP permissions."""
    ok = True

    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Recipe",
           "metadata": {"name": "cyclic-recipe"},
           "spec": {"coreMetadata": {"description": "x", "type": "workflow",
                                     "version": "1.0.0", "status": "active"},
                    "executionDefinition": {"stages": [
                        {"id": "a", "name": "A", "type": "ingest", "dependsOn": ["b"]},
                        {"id": "b", "name": "B", "type": "transform", "dependsOn": ["a"]}]}}},
          context, namespace)
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Recipe",
           "metadata": {"name": "dangling-recipe"},
           "spec": {"coreMetadata": {"description": "x", "type": "workflow",
                                     "version": "1.0.0", "status": "active"},
                    "executionDefinition": {"stages": [
                        {"id": "a", "name": "A", "type": "ingest", "dependsOn": ["nowhere"]}]}}},
          context, namespace)
    _run_controller(kubeconfig, namespace)

    cyclic = _status_of(context, namespace, "recipes", "cyclic-recipe")
    ok &= record("Recipe rejects a dependency cycle",
                 PASS if cyclic.get("state") == "failed" and "cycle" in cyclic.get("message", "")
                 else FAIL, json.dumps(cyclic)[:160])

    dangling = _status_of(context, namespace, "recipes", "dangling-recipe")
    ok &= record("Recipe rejects an unknown dependency",
                 PASS if dangling.get("state") == "failed"
                 and "unknown" in dangling.get("message", "") else FAIL,
                 json.dumps(dangling)[:160])

    _, out, _ = kubectl("get", "role", "default-idp", "-n", namespace, "-o", "json",
                        context=context)
    verbs = set(json.loads(out)["rules"][0]["verbs"])
    ok &= record("a deny-by-default AgentIdP grants read-only access",
                 PASS if verbs == {"get", "list", "watch"} else FAIL, str(sorted(verbs)))

    kubectl("patch", "agentidps", "default-idp", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"defaultBehavior": "allow"}}), context=context)
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "role", "default-idp", "-n", namespace, "-o", "json",
                        context=context)
    verbs = set(json.loads(out)["rules"][0]["verbs"])
    ok &= record("an allow-by-default AgentIdP grants writes",
                 PASS if "create" in verbs and "patch" in verbs else FAIL, str(sorted(verbs)))
    return ok


def test_controller_remaining_status(context, namespace, kubeconfig):
    """The kinds whose status is only exercised by behaviour tests."""
    _pin_metrics(context, namespace, {"gateway-tokens": 1_000_000, "request-rate": 1500,
                                      "request-latency": 42})
    kubectl("patch", "aimetrics", "request-latency", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"status": "active"}}), context=context)
    _run_controller(kubeconfig, namespace)

    ok = True
    for plural, name, fields in [
        ("models", "llama-3-70b-instruct", ["hubModelId"]),
        ("trainloops", "sft-nightly", ["schedule"]),
        ("aimeters", "tenant-token-spend", ["currentUsage", "unit", "window"]),
        ("guardrails", "throttle-high-rps", ["triggered", "observations"]),
        ("modelautoscalers", "llama-3-70b-autoscaler", ["currentReplicas", "metrics"]),
        ("harnessswarmautoscalers", "api-harness-swarm", ["currentReplicas", "metrics"]),
        ("toolserverautoscalers", "summarize-text-autoscaler", ["currentReplicas", "metrics"]),
    ]:
        if plural == "trainloops":
            kubectl("patch", plural, name, "-n", namespace, "--type=merge",
                    "-p", json.dumps({"spec": {"status": "active"}}), context=context)
            _run_controller(kubeconfig, namespace)
        block = _status_of(context, namespace, plural, name)
        missing = [f for f in fields if f not in block]
        ok &= record(f"status of {plural}/{name}",
                     PASS if not missing and block.get("conditions") else FAIL,
                     f"missing {missing} in {json.dumps(block)[:140]}")

    metric = _status_of(context, namespace, "aimetrics", "request-latency")
    ok &= record("AIMetric reports the observed value",
                 PASS if metric.get("currentValue") == 42 else FAIL, json.dumps(metric)[:160])
    return ok


def test_metric_source_prometheus(context, namespace, kubeconfig):
    """The Prometheus backend is used when configured, and falls back when not."""
    sys.path.insert(0, str(ROOT))
    from controller.context import Context
    from controller.metrics import MetricSource

    _pin_metrics(context, namespace, {"fallback-metric": 7})

    ctx = Context(kubeconfig=kubeconfig, namespace=namespace,
                  prometheus_url="http://127.0.0.1:1")  # nothing listening
    source = MetricSource(ctx)
    ok = record("an unreachable Prometheus falls back to the ConfigMap",
                PASS if source.value("fallback-metric", namespace) == 7 else FAIL)
    ok &= record("an unknown metric reports no value",
                 PASS if source.value("no-such-metric", namespace) is None else FAIL)
    return ok



# --------------------------------------------------------------------------- 9
def test_controller_drift(context, namespace, kubeconfig):
    """A controller that does not correct drift is not a controller."""
    ok = True
    _run_controller(kubeconfig, namespace)

    # delete a child by hand
    kubectl("delete", "service", "api-harness", "-n", namespace, context=context)
    _run_controller(kubeconfig, namespace)
    code, _, err = kubectl("get", "service", "api-harness", "-n", namespace,
                           context=context, check=False)
    ok &= record("a deleted Service is recreated", PASS if code == 0 else FAIL,
                 err.strip()[:140])

    kubectl("delete", "deployment", "api-harness", "-n", namespace, context=context)
    wait_gone("deployment", "api-harness", context, namespace, 60)
    _run_controller(kubeconfig, namespace)
    code, _, err = kubectl("get", "deployment", "api-harness", "-n", namespace,
                           context=context, check=False)
    ok &= record("a deleted Deployment is recreated", PASS if code == 0 else FAIL,
                 err.strip()[:140])

    # edit a child by hand
    kubectl("set", "image", "deployment/api-harness", "api-harness=nginx:1.27",
            "-n", namespace, context=context)
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "deployment", "api-harness", "-n", namespace,
                        "-o", "jsonpath={.spec.template.spec.containers[0].image}",
                        context=context)
    ok &= record("a hand-edited image is reverted to the spec",
                 PASS if out.strip() == "acme/support-agent:1.4.0" else FAIL, out.strip())

    kubectl("patch", "configmap", "summarize-text-tools", "-n", namespace, "--type=merge",
            "-p", json.dumps({"data": {"catalog.json": "{}"}}), context=context)
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "configmap", "summarize-text-tools", "-n", namespace,
                        "-o", "jsonpath={.data.catalog\\.json}", context=context)
    ok &= record("a hand-edited ConfigMap is rewritten",
                 PASS if "summarize-text" in out else FAIL, out[:100])

    # the spec is the source of truth for replicas too
    kubectl("scale", "deployment/api-harness", "--replicas=99", "-n", namespace,
            context=context)
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "deployment", "api-harness", "-n", namespace,
                        "-o", "jsonpath={.spec.replicas}", context=context)
    declared = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    ok &= record("a hand-scaled Deployment is reset to the declared count",
                 PASS if out.strip() == str(declared) else FAIL,
                 f"deployment={out.strip()} declared={declared}")
    return ok


def test_controller_updates(context, namespace, kubeconfig):
    """Editing a spec rolls the workload."""
    ok = True
    kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"code": {"image": "acme/support-agent:2.0.0"}}}),
            context=context)
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "deployment", "api-harness", "-n", namespace,
                        "-o", "jsonpath={.spec.template.spec.containers[0].image}",
                        context=context)
    ok &= record("updating the image rolls the Deployment",
                 PASS if out.strip() == "acme/support-agent:2.0.0" else FAIL, out.strip())

    kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"endpoints": [
                {"name": "api", "port": 9090, "path": "/api"}]}}), context=context)
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "service", "api-harness", "-n", namespace,
                        "-o", "jsonpath={.spec.ports[0].port}", context=context)
    ok &= record("changing an endpoint updates the Service port",
                 PASS if out.strip() == "9090" else FAIL, out.strip())

    kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"env": {"ROLLED": "yes"}}}), context=context)
    _run_controller(kubeconfig, namespace)
    _, out, _ = kubectl("get", "deployment", "api-harness", "-n", namespace,
                        "-o", "jsonpath={.spec.template.spec.containers[0].env}",
                        context=context)
    ok &= record("env changes reach the container", PASS if "ROLLED" in out else FAIL, out[:120])

    # observedGeneration tracks the spec
    _, out, _ = kubectl("get", "harnessruntimes", "api-harness", "-n", namespace,
                        "-o", "json", context=context)
    doc = json.loads(out)
    ok &= record("observedGeneration tracks metadata.generation",
                 PASS if doc["status"].get("observedGeneration") == doc["metadata"]["generation"]
                 else FAIL,
                 f"observed={doc['status'].get('observedGeneration')} "
                 f"generation={doc['metadata']['generation']}")

    # restore
    kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"code": {"image": "acme/support-agent:1.4.0"},
                                       "endpoints": [{"name": "api", "port": 8080,
                                                      "path": "/api"}]}}), context=context)
    _run_controller(kubeconfig, namespace)
    return ok


def test_controller_garbage_collection(context, namespace, kubeconfig):
    """Deleting an AgentBox object removes everything it owns."""
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "ToolServer",
           "metadata": {"name": "doomed-tools"},
           "spec": {"code": {"image": "busybox:1.36"}, "endpoint": {"port": 8080},
                    "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}},
                    "tools": [{"name": "noop", "parameters": {"type": "object"},
                               "returns": {"type": "object"}}]}},
          context, namespace)
    _run_controller(kubeconfig, namespace)

    ok = True
    for resource in ("deployment", "service", "configmap"):
        name = "doomed-tools-tools" if resource == "configmap" else "doomed-tools"
        code, _, _ = kubectl("get", resource, name, "-n", namespace, context=context, check=False)
        ok &= record(f"doomed ToolServer has a {resource}", PASS if code == 0 else FAIL)

    kubectl("delete", "toolserver", "doomed-tools", "-n", namespace, context=context)
    for resource in ("deployment", "service", "configmap"):
        name = "doomed-tools-tools" if resource == "configmap" else "doomed-tools"
        ok &= record(f"garbage collection removes the {resource}",
                     PASS if wait_gone(resource, name, context, namespace, 90) else FAIL)
    return ok


def test_controller_watch_loop(context, namespace, kubeconfig):
    """The watch loop and worker threads, not just run_once()."""
    sys.path.insert(0, str(ROOT))
    from controller.context import Context
    from controller.manager import Manager
    import threading

    manager = Manager(Context(kubeconfig=kubeconfig, namespace=namespace),
                      resync_seconds=5, workers=2)
    thread = threading.Thread(target=manager.run, daemon=True)
    thread.start()
    time.sleep(3)

    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Dataset",
           "metadata": {"name": "watched-dataset"},
           "spec": {"name": "watched", "type": "fs", "direction": "source", "enabled": True,
                    "config": {"fs": {"basePath": "/data"}}}},
          context, namespace)

    deadline = time.time() + 60
    reconciled = False
    while time.time() < deadline:
        code, out, _ = kubectl("get", "datasets", "watched-dataset", "-n", namespace,
                               "-o", "json", context=context, check=False)
        if code == 0 and json.loads(out).get("status", {}).get("state") == "active":
            reconciled = True
            break
        time.sleep(2)

    ok = record("the watch loop reconciles a newly created object",
                PASS if reconciled else FAIL, "no status within 60s")

    # an edit is picked up too
    kubectl("patch", "datasets", "watched-dataset", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"enabled": False}}), context=context)
    deadline = time.time() + 60
    suspended = False
    while time.time() < deadline:
        _, out, _ = kubectl("get", "datasets", "watched-dataset", "-n", namespace,
                            "-o", "json", context=context)
        if json.loads(out).get("status", {}).get("state") == "suspended":
            suspended = True
            break
        time.sleep(2)
    ok &= record("the watch loop picks up an edit",
                 PASS if suspended else FAIL, "state did not change within 60s")

    manager.stop.set()
    thread.join(timeout=10)
    ok &= record("the controller shuts down cleanly",
                 PASS if not thread.is_alive() else FAIL, "run() did not return")
    ok &= record("the watch loop recorded no failures",
                 PASS if manager.failures == 0 else FAIL, f"{manager.failures} failures")
    return ok


def test_leader_election(context, namespace, kubeconfig):
    """Two replicas: one leads, the other stands by."""
    sys.path.insert(0, str(ROOT))
    from controller.context import Context
    from controller.leader import LeaderElector

    ctx = Context(kubeconfig=kubeconfig, namespace=namespace)
    first = LeaderElector(ctx, namespace=namespace, identity="replica-a")
    second = LeaderElector(ctx, namespace=namespace, identity="replica-b")

    ok = record("the first replica acquires the lease",
                PASS if first.try_acquire() else FAIL)
    ok &= record("the second replica does not",
                 PASS if not second.try_acquire() else FAIL)
    ok &= record("the holder can renew", PASS if first.try_acquire() else FAIL)

    first.is_leader.set()
    first.release()
    ok &= record("a released lease is taken by the standby",
                 PASS if second.try_acquire() else FAIL)

    kubectl("delete", "lease", "agentbox-controller", "-n", namespace,
            context=context, check=False)
    return ok


def test_scaling_policies(context, namespace, kubeconfig):
    """Rate-limit policies cap how fast a target moves."""
    ok = True
    # Recreate the autoscaler so status.lastScaleTime starts empty; a scale from
    # an earlier stage would otherwise gate the first step under a long period.
    kubectl("delete", "harnessswarmautoscalers", "api-harness-swarm", "-n", namespace,
            context=context, check=False)
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessSwarmAutoScaler",
           "metadata": {"name": "api-harness-swarm"},
           "spec": {"scaleTargetRef": {"kind": "HarnessRuntime", "name": "api-harness"},
                    "bounds": {"minReplicas": 1, "maxReplicas": 50},
                    "metrics": [{"type": "aiMetric", "metric": "pending-agent-sessions",
                                 "target": {"metricType": "averageValue", "value": 5}}],
                    "behavior": {"scaleUp": {"stabilizationWindowSeconds": 0,
                                             "policies": [{"type": "pods", "value": 2,
                                                           "periodSeconds": 300}]},
                                 "scaleDown": {"stabilizationWindowSeconds": 0,
                                               "selectPolicy": "disabled"}}}},
          context, namespace)
    kubectl("patch", "harnessruntimes", "api-harness", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 2}}), context=context)
    _pin_metrics(context, namespace, {"pending-agent-sessions": 50})
    _run_controller(kubeconfig, namespace)

    replicas = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    ok &= record("a pods policy caps one scale-up step at +2",
                 PASS if replicas == 4 else FAIL, f"replicas={replicas}")

    # the policy period gates the next change, so a pass inside it must not move
    _run_controller(kubeconfig, namespace)
    gated = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    swarm = _status_of(context, namespace, "harnessswarmautoscalers", "api-harness-swarm")
    ok &= record("a pass inside the policy period does not move the target",
                 PASS if gated == 4 and "policy period" in swarm.get("message", "") else FAIL,
                 f"replicas={gated}: {swarm.get('message')}")

    # shorten the period so the next step is allowed, rather than racing a clock
    kubectl("patch", "harnessswarmautoscalers", "api-harness-swarm", "-n", namespace,
            "--type=merge", "-p", json.dumps({"spec": {"behavior": {"scaleUp": {
                "stabilizationWindowSeconds": 0,
                "policies": [{"type": "pods", "value": 2, "periodSeconds": 1}]}}}}),
            context=context)
    time.sleep(2)
    _run_controller(kubeconfig, namespace)
    ok &= record("the next pass after the period steps by 2 again",
                 PASS if _spec_replicas(context, namespace, "harnessruntimes",
                                        "api-harness") == 6 else FAIL,
                 str(_spec_replicas(context, namespace, "harnessruntimes", "api-harness")))

    _pin_metrics(context, namespace, {"pending-agent-sessions": 0})
    time.sleep(2)
    _run_controller(kubeconfig, namespace)
    held = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    swarm = _status_of(context, namespace, "harnessswarmautoscalers", "api-harness-swarm")
    ok &= record("selectPolicy disabled blocks scale-down entirely",
                 PASS if held == 6 and "disabled" in swarm.get("message", "") else FAIL,
                 f"replicas={held}: {swarm.get('message')}")

    # percent policy
    kubectl("patch", "harnessswarmautoscalers", "api-harness-swarm", "-n", namespace,
            "--type=merge", "-p", json.dumps({"spec": {"behavior": {
                "scaleUp": {"stabilizationWindowSeconds": 0,
                            "policies": [{"type": "percent", "value": 50,
                                          "periodSeconds": 1}]},
                "scaleDown": {"selectPolicy": "max"}}}}), context=context)
    _pin_metrics(context, namespace, {"pending-agent-sessions": 50})
    before = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    time.sleep(2)
    _run_controller(kubeconfig, namespace)
    after = _spec_replicas(context, namespace, "harnessruntimes", "api-harness")
    ok &= record("a percent policy caps the step at +50%",
                 PASS if after == int(before * 1.5) else FAIL, f"{before} -> {after}")
    return ok


def test_scaling_clamps(context, namespace, kubeconfig):
    """Bounds hold in both directions, and conflicting metrics resolve to the max."""
    ok = True
    kubectl("patch", "modelautoscalers", "llama-3-70b-autoscaler", "-n", namespace,
            "--type=merge", "-p", json.dumps({"spec": {
                "enabled": True,
                "bounds": {"minReplicas": 2, "maxReplicas": 5},
                "behavior": {"scaleUp": {"stabilizationWindowSeconds": 0},
                             "scaleDown": {"stabilizationWindowSeconds": 0}},
                "metrics": [{"type": "aiMetric", "metric": "inference-queue-depth",
                             "target": {"metricType": "averageValue", "value": 20}}]}}),
            context=context)
    kubectl("patch", "models", "llama-3-70b-instruct", "-n", namespace, "--type=merge",
            "-p", json.dumps({"spec": {"replicas": 3}}), context=context)

    _pin_metrics(context, namespace, {"inference-queue-depth": 500})
    _run_controller(kubeconfig, namespace)
    ok &= record("demand above maxReplicas clamps to the ceiling",
                 PASS if _spec_replicas(context, namespace, "models",
                                        "llama-3-70b-instruct") == 5 else FAIL)

    _pin_metrics(context, namespace, {"inference-queue-depth": 1})
    _run_controller(kubeconfig, namespace)
    ok &= record("demand below minReplicas clamps to the floor",
                 PASS if _spec_replicas(context, namespace, "models",
                                        "llama-3-70b-instruct") == 2 else FAIL)

    _pin_metrics(context, namespace, {"inference-queue-depth": 40, "quality-score": 1})
    kubectl("patch", "modelautoscalers", "llama-3-70b-autoscaler", "-n", namespace,
            "--type=merge", "-p", json.dumps({"spec": {"metrics": [
                {"type": "aiMetric", "metric": "inference-queue-depth",
                 "target": {"metricType": "averageValue", "value": 20}},
                {"type": "aiMetric", "metric": "quality-score",
                 "target": {"metricType": "averageValue", "value": 100}}]}}),
            context=context)
    _run_controller(kubeconfig, namespace)
    ok &= record("conflicting metrics resolve to the highest demand",
                 PASS if _spec_replicas(context, namespace, "models",
                                        "llama-3-70b-instruct") == 4 else FAIL,
                 str(_spec_replicas(context, namespace, "models", "llama-3-70b-instruct")))

    _pin_metrics(context, namespace, {"nothing": 1})
    before = _spec_replicas(context, namespace, "models", "llama-3-70b-instruct")
    _run_controller(kubeconfig, namespace)
    after = _spec_replicas(context, namespace, "models", "llama-3-70b-instruct")
    autoscaler = _status_of(context, namespace, "modelautoscalers", "llama-3-70b-autoscaler")
    ok &= record("no metric value leaves the target alone",
                 PASS if before == after and autoscaler.get("state") == "pending" else FAIL,
                 f"{before} -> {after}, state={autoscaler.get('state')}")
    return ok


def test_metering_pricing_models(context, namespace, kubeconfig):
    """Flat, per-unit and tiered pricing, thresholds and attribution."""
    ok = True

    # tiered: 1.5e9 tokens over tiers at 1e9 (0.5) then the rest at 1.0 per 1e6
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "AIMeter",
           "metadata": {"name": "tiered-meter"},
           "spec": {"usage": {"unit": "totalTokens", "source": {"metric": "tiered-tokens"}},
                    "window": {"type": "billingPeriod", "period": "monthly"},
                    "pricing": {"currency": "USD", "model": "tiered", "perUnits": 1000000,
                                "unitPrice": 1.0,
                                "tiers": [{"upTo": 1000000000, "unitPrice": 0.5}]},
                    "budget": {"limit": 2000, "limitType": "cost",
                               "alertThresholdsPercent": [50, 80]}}},
          context, namespace)
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "AIMeter",
           "metadata": {"name": "usage-budget-meter"},
           "spec": {"usage": {"unit": "requests", "source": {"metric": "request-count"}},
                    "window": {"type": "tumbling", "durationSeconds": 3600},
                    "budget": {"limit": 100, "limitType": "usage"}}},
          context, namespace)
    _pin_metrics(context, namespace, {
        "tiered-tokens": 1_500_000_000,
        "request-count": 150,
        "gateway-tokens": 2_000_000_000,
        "gateway-tokens.tenant_id.acme": 1_500_000_000,
        "gateway-tokens.tenant_id.globex": 500_000_000,
    })
    _run_controller(kubeconfig, namespace)

    tiered = _status_of(context, namespace, "aimeters", "tiered-meter")
    ok &= record("tiered pricing charges each tier at its own rate (1000 USD)",
                 PASS if tiered.get("currentCost") == 1000 else FAIL, json.dumps(tiered)[:180])

    usage_budget = _status_of(context, namespace, "aimeters", "usage-budget-meter")
    ok &= record("a usage-type budget measures units, not cost",
                 PASS if usage_budget.get("budgetExceeded") is True
                 and usage_budget.get("currentCost") is None else FAIL,
                 json.dumps(usage_budget)[:180])

    meter = _status_of(context, namespace, "aimeters", "tenant-token-spend")
    attributed = meter.get("attributedUsage") or []
    ok &= record("usage is attributed per dimension",
                 PASS if len(attributed) == 2
                 and any(a["dimensions"] == "tenant_id=acme" for a in attributed) else FAIL,
                 json.dumps(attributed)[:200])

    _, out, _ = kubectl("get", "events", "-n", namespace, "-o", "json", context=context)
    reasons = {e["reason"] for e in json.loads(out)["items"]}
    ok &= record("crossing an alert threshold emits BudgetThreshold",
                 PASS if "BudgetThreshold" in reasons else FAIL, str(sorted(reasons)))
    return ok


def test_guardrail_logic(context, namespace, kubeconfig):
    """Operators, all/any, cooldown and suppression."""
    ok = True
    for operator, threshold, observed, expected in [
        ("gt", 100, 150, True), ("gt", 100, 50, False),
        ("gte", 100, 100, True), ("lt", 100, 50, True),
        ("lte", 100, 100, True), ("eq", 100, 100, True), ("neq", 100, 100, False),
    ]:
        name = f"op-{operator}-{observed}"
        apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Guardrail",
               "metadata": {"name": name},
               "spec": {"name": name, "status": "enforce", "priority": 1,
                        "conditions": {"all": [{"metric": "probe-metric",
                                                "operator": operator, "threshold": threshold,
                                                "statistic": "Average", "periodSeconds": 60}]},
                        "effect": {"type": "throttle"}}},
              context, namespace)
        _pin_metrics(context, namespace, {"probe-metric": observed})
        _run_controller(kubeconfig, namespace)
        result = _status_of(context, namespace, "guardrails", name)
        ok &= record(f"operator {operator}: {observed} vs {threshold} -> {expected}",
                     PASS if result.get("triggered") is expected else FAIL,
                     json.dumps(result)[:140])

    # all: every condition must hold; any: one is enough
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Guardrail",
           "metadata": {"name": "all-conditions"},
           "spec": {"name": "all", "status": "enforce", "priority": 1,
                    "conditions": {"all": [
                        {"metric": "probe-metric", "operator": "gt", "threshold": 10,
                         "statistic": "Average", "periodSeconds": 60},
                        {"metric": "probe-metric", "operator": "gt", "threshold": 10000,
                         "statistic": "Average", "periodSeconds": 60}]},
                    "effect": {"type": "throttle"}}},
          context, namespace)
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Guardrail",
           "metadata": {"name": "any-conditions"},
           "spec": {"name": "any", "status": "enforce", "priority": 1,
                    "conditions": {"any": [
                        {"metric": "probe-metric", "operator": "gt", "threshold": 10,
                         "statistic": "Average", "periodSeconds": 60},
                        {"metric": "probe-metric", "operator": "gt", "threshold": 10000,
                         "statistic": "Average", "periodSeconds": 60}]},
                    "effect": {"type": "throttle"}}},
          context, namespace)
    _pin_metrics(context, namespace, {"probe-metric": 100})
    _run_controller(kubeconfig, namespace)
    ok &= record("all: one false condition means not tripped",
                 PASS if _status_of(context, namespace, "guardrails",
                                    "all-conditions").get("triggered") is False else FAIL)
    ok &= record("any: one true condition is enough",
                 PASS if _status_of(context, namespace, "guardrails",
                                    "any-conditions").get("triggered") is True else FAIL)

    # a missing metric must not read as "not tripped"
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Guardrail",
           "metadata": {"name": "no-metric-guardrail"},
           "spec": {"name": "nm", "status": "enforce", "priority": 1,
                    "conditions": {"all": [{"metric": "absent-metric", "operator": "gt",
                                            "threshold": 1, "statistic": "Average",
                                            "periodSeconds": 60}]},
                    "effect": {"type": "deny"}}},
          context, namespace)
    _run_controller(kubeconfig, namespace)
    ok &= record("a missing metric reports pending, not a false negative",
                 PASS if _status_of(context, namespace, "guardrails",
                                    "no-metric-guardrail").get("state") == "pending" else FAIL)

    # suppression keeps a flapping guardrail quiet
    apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Guardrail",
           "metadata": {"name": "suppressed-guardrail"},
           "spec": {"name": "sup", "status": "enforce", "priority": 1,
                    "suppressForSeconds": 3600,
                    "conditions": {"all": [{"metric": "probe-metric", "operator": "gt",
                                            "threshold": 10, "statistic": "Average",
                                            "periodSeconds": 60}]},
                    "effect": {"type": "throttle"}}},
          context, namespace)
    _run_controller(kubeconfig, namespace)
    first = _status_of(context, namespace, "guardrails", "suppressed-guardrail")
    _run_controller(kubeconfig, namespace)
    second = _status_of(context, namespace, "guardrails", "suppressed-guardrail")
    ok &= record("a guardrail fires once, then suppresses",
                 PASS if first.get("triggered") is True and second.get("suppressed") is True
                 else FAIL, f"{json.dumps(first)[:90]} then {json.dumps(second)[:90]}")
    return ok


def test_dataset_connectors(context, namespace, kubeconfig):
    """Every connector variant publishes a usable address."""
    variants = [
        ("httpPoll", {"url": "https://api.example.com/orders", "method": "GET",
                      "intervalSeconds": 60}, "https://api.example.com/orders"),
        ("kafka", {"brokers": ["kafka-0:9092", "kafka-1:9092"],
                   "topics": {"consume": ["events"]},
                   "consumerGroup": "agentbox"}, "kafka-0:9092,kafka-1:9092"),
        ("s3", {"bucket": "agent-artifacts", "prefix": "runs", "region": "us-east-1"},
         "s3://agent-artifacts/runs"),
        ("fs", {"basePath": "/mnt/data"}, "/mnt/data"),
        ("database", {"driver": "postgres",
                      "connection": {"host": "postgres.svc", "port": 5432,
                                     "database": "agents"}}, "postgres.svc"),
    ]
    ok = True
    for kind, config, expected in variants:
        name = f"ds-{kind.lower()}"
        code, _, err = apply(
            {"apiVersion": "ai.agentbox.io/v1beta1", "kind": "Dataset",
             "metadata": {"name": name},
             "spec": {"name": name, "type": kind, "direction": "source", "enabled": True,
                      "config": {kind: config}}},
            context, namespace, check=False)
        if code != 0:
            ok &= record(f"Dataset {kind} accepted", FAIL, err.strip().splitlines()[-1][:160])
            continue
        _run_controller(kubeconfig, namespace)
        address = _status_of(context, namespace, "datasets", name).get("address")
        ok &= record(f"Dataset {kind} resolves its address",
                     PASS if address == expected else FAIL, f"{address} != {expected}")
    return ok


def test_api_edge_validation(context, namespace):
    """Pattern and format rules the schemas promise."""
    ok = True
    cases = [
        ("an invalid cron expression is rejected", {
            "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
            "metadata": {"name": "bad-cron-pattern"},
            "spec": {"runtimeKind": "cron", "code": {"image": "busybox:1.36"},
                     "schedule": {"cronExpression": "every tuesday please"}}}),
        ("a non-DNS-1123 name is rejected", {
            "apiVersion": "ai.agentbox.io/v1beta1", "kind": "Model",
            "metadata": {"name": "Not_A_Valid_Name"},
            "spec": {"modelName": "x", "modelHub": "huggingface", "hubModelId": "org/x"}}),
        ("a malformed env var name is rejected", {
            "apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
            "metadata": {"name": "bad-env"},
            "spec": {"runtimeKind": "worker", "code": {"image": "busybox:1.36"},
                     "env": {"not-a-valid-var": "x"}}}),
        ("a tumbling window without a duration is rejected", {
            "apiVersion": "ai.agentbox.io/v1beta1", "kind": "AIMeter",
            "metadata": {"name": "bad-window"},
            "spec": {"usage": {"unit": "requests", "source": {"metric": "m"}},
                     "window": {"type": "tumbling"}}}),
        ("tiered pricing without tiers is rejected", {
            "apiVersion": "ai.agentbox.io/v1beta1", "kind": "AIMeter",
            "metadata": {"name": "bad-tiers"},
            "spec": {"usage": {"unit": "requests", "source": {"metric": "m"}},
                     "window": {"type": "tumbling", "durationSeconds": 60},
                     "pricing": {"model": "tiered", "unitPrice": 1}}}),
        ("a wrong apiVersion is rejected", {
            "apiVersion": "ai.agentbox.io/v1alpha1", "kind": "Model",
            "metadata": {"name": "old-version"},
            "spec": {"modelName": "x", "modelHub": "huggingface", "hubModelId": "org/x"}}),
    ]
    for label, doc in cases:
        code, _, _ = apply(doc, context, namespace, check=False)
        if code == 0:
            kubectl("delete", doc["kind"].lower() + "s", doc["metadata"]["name"],
                    "-n", namespace, context=context, check=False)
        ok &= record(label, PASS if code != 0 else FAIL, "the API server accepted it")

    # a worker harness needs no endpoint
    code, _, err = apply({"apiVersion": "ai.agentbox.io/v1beta1", "kind": "HarnessRuntime",
                          "metadata": {"name": "worker-harness"},
                          "spec": {"runtimeKind": "worker",
                                   "code": {"image": "busybox:1.36"},
                                   "compute": {"cpu": {"cores": 0.05, "memoryMb": 32}}}},
                         context, namespace, check=False)
    ok &= record("a worker harness without endpoints is accepted",
                 PASS if code == 0 else FAIL, err.strip()[:140])

    _, out, _ = kubectl("explain", "harnessruntime.spec.runtimeKind", context=context)
    ok &= record("kubectl explain renders field descriptions",
                 PASS if "Workload shape" in out else FAIL, out.strip()[:140])
    return ok



# -------------------------------------------------------------------------- 10
def _wait_for(condition, timeout, interval=5):
    """Poll a callable until it returns truthy, or the timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    return None


def test_demo_real_request(context, namespace, kubeconfig):
    """
    The whole point, proven with real processes: an agent discovers a tool,
    calls it, calls a gateway, and reaches a model behind it. Then a budget is
    spent and the same agent is refused — without its image changing.
    """
    demo_ns = "agentbox-demo-e2e"
    kubectl("create", "namespace", demo_ns, context=context, check=False)

    manifest = (ROOT / "examples" / "demo.yaml").read_text().replace(
        "agentbox-demo.svc", f"{demo_ns}.svc")
    kubectl("apply", "-n", demo_ns, "-f", "-", context=context, input_text=manifest)
    _run_controller(kubeconfig, demo_ns)

    ok = True
    ready = _wait_for(
        lambda: all(
            kubectl("get", "deployment", name, "-n", demo_ns,
                    "-o", "jsonpath={.status.readyReplicas}",
                    context=context, check=False)[1].strip() == "1"
            for name in ("demo-small", "demo-gateway", "demo-tools")),
        timeout=420)
    ok &= record("the demo model, gateway and tool server all come up",
                 PASS if ready else FAIL, "not ready within 300s")
    if not ready:
        kubectl("delete", "namespace", demo_ns, "--wait=false", context=context, check=False)
        return ok

    _run_controller(kubeconfig, demo_ns)
    completed = _wait_for(
        lambda: kubectl("get", "job", "demo-agent", "-n", demo_ns,
                        "-o", "jsonpath={.status.succeeded}",
                        context=context, check=False)[1].strip() == "1",
        timeout=240)
    _, logs, _ = kubectl("logs", "job/demo-agent", "-n", demo_ns, context=context, check=False)

    ok &= record("the agent job completes", PASS if completed else FAIL, logs[-300:])
    ok &= record("the agent discovers the tool from the published catalog",
                 PASS if "discovered tool: summarize" in logs else FAIL, logs[-200:])
    ok &= record("the tool server answers a real call",
                 PASS if '"wordCount": 9' in logs else FAIL, logs[-200:])
    ok &= record("the gateway routes to the model and returns a completion",
                 PASS if "RESULT ok:" in logs else FAIL, logs[-200:])

    # the generated config must be usable by a real LiteLLM, not just by our demo
    _, config, _ = kubectl("get", "configmap", "demo-gateway-config", "-n", demo_ns,
                           "-o", "jsonpath={.data.config\\.json}", context=context)
    rendered = json.loads(config)
    params = rendered["model_list"][0]["litellm_params"]
    ok &= record("the gateway config is rendered in LiteLLM's dialect",
                 PASS if "api_base" in params and "apiBase" not in params else FAIL,
                 json.dumps(params))

    # spend the budget: 50,000 tokens at 1 USD per 1,000 against a 10 USD limit
    kubectl("create", "configmap", "agentbox-metrics", "--from-literal=demo-tokens=50000",
            "-n", demo_ns, context=context, check=False)
    _run_controller(kubeconfig, demo_ns)

    meter = _status_of(context, demo_ns, "aimeters", "demo-spend")
    ok &= record("the meter prices the usage and flags the breach",
                 PASS if meter.get("currentCost") == 50 and meter.get("budgetExceeded")
                 else FAIL, json.dumps(meter)[:180])

    gateway = _status_of(context, demo_ns, "gateways", "demo-gateway")
    ok &= record("the gateway is told to enforce the breach",
                 PASS if "demo-spend" in (gateway.get("enforcing") or []) else FAIL,
                 json.dumps(gateway)[:180])

    seen = _wait_for(
        lambda: "budget of 10 exceeded" in kubectl(
            "exec", "deploy/demo-gateway", "-n", demo_ns, "--",
            "cat", "/etc/agentbox/config.json", context=context, check=False)[1],
        timeout=180)
    ok &= record("the running gateway picks up the enforcement",
                 PASS if seen else FAIL, "config did not refresh within 180s")

    kubectl("delete", "job", "demo-agent", "-n", demo_ns, context=context, check=False)
    _run_controller(kubeconfig, demo_ns)
    _wait_for(
        lambda: kubectl("get", "job", "demo-agent", "-n", demo_ns,
                        "-o", "jsonpath={.status.succeeded}",
                        context=context, check=False)[1].strip() == "1",
        timeout=240)
    _, blocked_logs, _ = kubectl("logs", "job/demo-agent", "-n", demo_ns,
                                 context=context, check=False)

    ok &= record("the same agent is refused once the budget is spent",
                 PASS if "RESULT blocked: AIMeter/demo-spend" in blocked_logs else FAIL,
                 blocked_logs[-300:])
    ok &= record("the refusal names the policy that caused it",
                 PASS if '"type": "throttle"' in blocked_logs else FAIL,
                 blocked_logs[-200:])

    kubectl("delete", "namespace", demo_ns, "--wait=false", context=context, check=False)
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
    parser.add_argument("--skip-demo", action="store_true",
                        help="skip examples/demo.yaml, which pulls real images")
    parser.add_argument("--only", help="run only stages whose label contains this string")
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

    # a namespace still terminating from a previous run would swallow what we create
    for _ in range(60):
        code, _, _ = kubectl("get", "namespace", args.namespace,
                             context=args.context, check=False)
        if code != 0:
            break
        time.sleep(2)
    kubectl("create", "namespace", args.namespace, context=args.context, check=False)

    stages = []

    # The demo runs real pods. It goes first so it is not competing for a small
    # node with the fixtures every other stage leaves behind.
    if not args.skip_demo:
        stages.append(("demo: a real request end to end", test_demo_real_request,
                       (args.context, args.namespace, args.kubeconfig)))

    stages += [
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
        ("controller reconcile", test_controller_reconciles,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller status", test_controller_status,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller scaling", test_controller_scaling,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller metering", test_controller_metering_and_guardrails,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller serving", test_controller_serving_paths,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller workload shapes", test_controller_workload_shapes,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller evaluator runs", test_controller_evaluator_run,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller scaling edges", test_controller_scaling_edges,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller suspended paths", test_controller_suspended_paths,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller validation paths", test_controller_validation_paths,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller remaining status", test_controller_remaining_status,
         (args.context, args.namespace, args.kubeconfig)),
        ("metric source", test_metric_source_prometheus,
         (args.context, args.namespace, args.kubeconfig)),
        ("api edge validation", test_api_edge_validation, (args.context, args.namespace)),
        ("controller drift", test_controller_drift,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller updates", test_controller_updates,
         (args.context, args.namespace, args.kubeconfig)),
        ("garbage collection", test_controller_garbage_collection,
         (args.context, args.namespace, args.kubeconfig)),
        ("dataset connectors", test_dataset_connectors,
         (args.context, args.namespace, args.kubeconfig)),
        ("scaling policies", test_scaling_policies,
         (args.context, args.namespace, args.kubeconfig)),
        ("scaling clamps", test_scaling_clamps,
         (args.context, args.namespace, args.kubeconfig)),
        ("metering pricing", test_metering_pricing_models,
         (args.context, args.namespace, args.kubeconfig)),
        ("guardrail logic", test_guardrail_logic,
         (args.context, args.namespace, args.kubeconfig)),
        ("leader election", test_leader_election,
         (args.context, args.namespace, args.kubeconfig)),
        ("watch loop", test_controller_watch_loop,
         (args.context, args.namespace, args.kubeconfig)),
        ("controller idempotence", test_controller_idempotent,
         (args.context, args.namespace, args.kubeconfig)),
    ]

    if args.only:
        stages = [s for s in stages if args.only in s[0]]
        print(f"running {len(stages)} stage(s) matching {args.only!r}\n")

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
