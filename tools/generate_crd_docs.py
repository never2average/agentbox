#!/usr/bin/env python3
"""
Generate docs/crd-reference.md from the AgentBox schemas.

The reference is derived from schemas/*.json so it cannot drift from the CRDs.

Usage:
    python tools/generate_crd_docs.py [--check]
"""
import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
TARGET = ROOT / "docs" / "crd-reference.md"

PLANES = OrderedDict([
    ("Serving plane", ["model", "model-autoscaler", "harness-runtime",
                       "harness-swarm-autoscaler", "agent-idp", "tool-server",
                       "tool-server-autoscaler", "gateway", "ai-metric", "ai-meter"]),
    ("Training plane", ["train-loop", "dataset", "evaluator", "guardrail",
                        "tracer", "recipe"]),
])

WORKLOADS = {
    "harness-runtime": "Deployment + Service, Job, or CronJob (by `spec.runtimeKind`)",
    "tool-server": "Deployment + Service",
    "train-loop": "Job, or CronJob when `spec.execution.schedule` is set",
}

ONE_LINERS = {
    "model": "A model the platform can serve or call: where the weights come from, what the model can do.",
    "model-autoscaler": "Scales a Model's replicas on queue depth, throughput or GPU utilisation.",
    "harness-runtime": "Runs a developer's agent image as a Kubernetes workload.",
    "harness-swarm-autoscaler": "Scales a swarm of harnesses on session and queue pressure.",
    "agent-idp": "Issues workload identity to agents and groups the policies that apply to them.",
    "tool-server": "Runs an image that serves tools over HTTP or gRPC, and publishes their contracts.",
    "tool-server-autoscaler": "Scales a ToolServer on call rate, concurrency and latency.",
    "gateway": "Routes model traffic to a provider, with rate limits and per-model parameters.",
    "ai-metric": "Defines a metric derived from traces, other metrics, or a model judgement.",
    "ai-meter": "Turns metrics into attributed, priced, budgeted usage.",
    "train-loop": "Runs a training or evaluation job, once or on a schedule.",
    "dataset": "A connector-backed source or sink, with cursor state and checkpointing.",
    "evaluator": "An evaluation suite: cases plus how to score them.",
    "guardrail": "A metric-driven condition and the effect enforced when it trips.",
    "tracer": "Where traces and logs are exported, in OpenTelemetry shape.",
    "recipe": "A composable multi-stage pipeline definition.",
}


def type_of(node, doc):
    """Human-readable type for a schema node, following one level of $ref."""
    if "$ref" in node:
        ref = node["$ref"]
        name = ref.split("/")[-1]
        if ref.startswith("common-schema.json"):
            return f"`{name}`"
        target = doc.get("definitions", {}).get(name, {})
        base = target.get("type")
        if base == "array":
            return "array"
        if "enum" in target:
            return "enum"
        return f"`{name}`" if base == "object" else (base or "object")
    if "enum" in node:
        return "enum"
    node_type = node.get("type")
    if node_type == "array":
        items = node.get("items", {})
        inner = type_of(items, doc) if items else "any"
        return f"array&lt;{inner}&gt;"
    return node_type or "object"


def describe(node, doc):
    """Short description for a field, falling back to the referenced definition."""
    if node.get("description"):
        return node["description"]
    if "$ref" in node and not node["$ref"].startswith("common-schema.json"):
        target = doc.get("definitions", {}).get(node["$ref"].split("/")[-1], {})
        return target.get("description", "")
    if "enum" in node:
        return "One of: " + ", ".join(f"`{v}`" for v in node["enum"])
    target = node.get("items", {})
    if "$ref" in target:
        return describe(target, doc)
    return ""


def collect_rules(node, found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        found.extend(node.get("x-agentbox-rules", []))
        for value in node.values():
            collect_rules(value, found)
    elif isinstance(node, list):
        for value in node:
            collect_rules(value, found)
    return found


def render_crd(group, doc):
    crd = doc["x-agentbox-crd"]
    spec = doc["properties"]["spec"]
    required = set(spec.get("required", []))
    lines = []

    lines.append(f"### {crd['kind']}")
    lines.append("")
    lines.append(ONE_LINERS.get(group, doc.get("description", "")))
    lines.append("")
    short = ", ".join(f"`{s}`" for s in crd.get("shortNames", [])) or "—"
    lines.append(f"| | |\n|---|---|")
    lines.append(f"| Resource | `{crd['plural']}.ai.agentbox.io` |")
    lines.append(f"| Short names | {short} |")
    lines.append(f"| Backing workload | {WORKLOADS.get(group, 'none — configuration only')} |")
    lines.append(f"| Schema | [`schemas/{group}-schema.json`](../schemas/{group}-schema.json) |")
    lines.append(f"| Manifest | [`crds/{group}.yaml`](../crds/{group}.yaml) |")
    lines.append("")

    lines.append("| Field | Type | Required | Description |")
    lines.append("|---|---|---|---|")
    for name, node in spec["properties"].items():
        mark = "yes" if name in required else ""
        desc = describe(node, doc).replace("\n", " ").strip()
        lines.append(f"| `{name}` | {type_of(node, doc)} | {mark} | {desc} |")
    lines.append("")

    rules = collect_rules(spec)
    if rules:
        lines.append("Enforced by the API server as CEL validation rules:")
        lines.append("")
        for rule in rules:
            lines.append(f"- {rule['message']}")
        lines.append("")

    for example in doc.get("examples", [])[:1]:
        lines.append("```yaml")
        lines.append(yaml.dump(json.loads(json.dumps(example)),
                               default_flow_style=False, sort_keys=False).rstrip())
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def build():
    docs = {}
    for f in sorted(SCHEMA_DIR.glob("*-schema.json")):
        group = f.name[:-len("-schema.json")]
        doc = json.load(f.open(), object_pairs_hook=OrderedDict)
        if "x-agentbox-crd" in doc:
            docs[group] = doc

    out = ["<!-- Generated by tools/generate_crd_docs.py. Edit the schemas, then regenerate. -->",
           "# CRD reference", "",
           "Every AgentBox resource is a Kubernetes object in the `ai.agentbox.io/v1beta1` API group",
           "with the same envelope:", "",
           "```yaml",
           "apiVersion: ai.agentbox.io/v1beta1",
           "kind: HarnessRuntime",
           "metadata:",
           "  name: support-agent",
           "spec: {}      # the fields below",
           "status: {}    # written by the platform, never by you",
           "```", "",
           "Field names are camelCase. `metadata.name` is the resource name.",
           "Sixteen kinds, two planes:", ""]

    for plane, groups in PLANES.items():
        out.append(f"- **{plane}** — " + ", ".join(f"[{docs[g]['x-agentbox-crd']['kind']}]"
                                                   f"(#{docs[g]['x-agentbox-crd']['kind'].lower()})"
                                                   for g in groups))
    out.append("")

    for plane, groups in PLANES.items():
        out.append(f"## {plane}")
        out.append("")
        for group in groups:
            out.append(render_crd(group, docs[group]))

    return "\n".join(out).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    content = build()
    TARGET.parent.mkdir(exist_ok=True)

    if args.check:
        if not TARGET.exists() or TARGET.read_text() != content:
            print(f"{TARGET.relative_to(ROOT)} is out of date")
            return 1
        print(f"{TARGET.relative_to(ROOT)} is up to date")
        return 0

    TARGET.write_text(content)
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
