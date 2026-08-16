#!/usr/bin/env python3
"""
Generate Kubernetes CustomResourceDefinition manifests from the AgentBox schemas.

Each schemas/<group>-schema.json becomes crds/<group>.yaml, an
apiextensions.k8s.io/v1 CRD whose openAPIV3Schema is a structural schema:
all $refs inlined, const folded into enum, conditionals lifted into CEL
validation rules, and free-form objects marked preserve-unknown-fields.

Usage:
    python tools/generate_crds.py [--check]

--check regenerates into memory and fails if crds/ is out of date.
"""
import argparse
import copy
import json
import sys
from collections import OrderedDict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"
CRD_DIR = ROOT / "crds"

GROUP = "ai.agentbox.io"
CATEGORIES = ["agentbox"]

# Formats apiextensions understands; anything else is dropped
ALLOWED_FORMATS = {"date", "date-time", "duration", "email", "hostname",
                   "ipv4", "ipv6", "cidr", "uri", "byte", "password"}

# Kinds whose replicas are managed through the scale subresource
SCALABLE = {
    "HarnessRuntime": ".spec.replicas",
    "ToolServer": ".spec.replicas",
    "Model": ".spec.replicas",
}

PRINTER_COLUMNS = {
    "Model": [("Hub", "string", ".spec.modelHub"), ("HubModel", "string", ".spec.hubModelId")],
    "ModelAutoScaler": [("Target", "string", ".spec.scaleTargetRef.name"),
                        ("Min", "integer", ".spec.bounds.minReplicas"),
                        ("Max", "integer", ".spec.bounds.maxReplicas")],
    "HarnessRuntime": [("Kind", "string", ".spec.runtimeKind"),
                       ("Image", "string", ".spec.code.image"),
                       ("Replicas", "integer", ".status.replicas"),
                       ("Ready", "integer", ".status.readyReplicas")],
    "HarnessSwarmAutoScaler": [("Target", "string", ".spec.scaleTargetRef.name"),
                               ("Min", "integer", ".spec.bounds.minReplicas"),
                               ("Max", "integer", ".spec.bounds.maxReplicas")],
    "AgentIdP": [("Status", "string", ".spec.status"),
                 ("Default", "string", ".spec.defaultBehavior")],
    "ToolServer": [("Image", "string", ".spec.code.image"),
                   ("Port", "integer", ".spec.endpoint.port"),
                   ("Replicas", "integer", ".status.replicas"),
                   ("Ready", "integer", ".status.readyReplicas")],
    "ToolServerAutoScaler": [("Target", "string", ".spec.scaleTargetRef.name"),
                             ("Min", "integer", ".spec.bounds.minReplicas"),
                             ("Max", "integer", ".spec.bounds.maxReplicas")],
    "Gateway": [("Model", "string", ".spec.modelName"),
                ("Upstream", "string", ".spec.litellmParams.model")],
    "AIMetric": [("Type", "string", ".spec.type"), ("Metric", "string", ".spec.metricName"),
                 ("Unit", "string", ".spec.unit")],
    "AIMeter": [("Unit", "string", ".spec.usage.unit"), ("Window", "string", ".spec.window.type"),
                ("Budget", "number", ".spec.budget.limit")],
    "TrainLoop": [("Type", "string", ".spec.type"), ("Image", "string", ".spec.worker.image"),
                  ("Mode", "string", ".spec.execution.mode")],
    "Dataset": [("Type", "string", ".spec.type"), ("Direction", "string", ".spec.direction")],
    "Evaluator": [("Dataset", "string", ".spec.dataset.type")],
    "Guardrail": [("Status", "string", ".spec.status"), ("Priority", "integer", ".spec.priority"),
                  ("Effect", "string", ".spec.effect.type")],
    "Tracer": [("Scope", "string", ".spec.scope.name")],
    "Recipe": [("Type", "string", ".spec.coreMetadata.type"),
               ("Status", "string", ".spec.coreMetadata.status")],
}


def load_schemas():
    """Load every schema keyed by file name."""
    return {f.name: json.load(f.open(), object_pairs_hook=OrderedDict)
            for f in sorted(SCHEMA_DIR.glob("*-schema.json"))}


def resolve_refs(node, doc, schemas, seen=()):
    """Inline every $ref; structural schemas cannot carry references."""
    if isinstance(node, list):
        return [resolve_refs(v, doc, schemas, seen) for v in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        target = node["$ref"]
        if target in seen:
            # Recursive definition (e.g. OTel typed values). Structural schemas
            # cannot recurse, so cut here and preserve whatever is nested.
            return OrderedDict([
                ("x-kubernetes-preserve-unknown-fields", True),
                ("description", node.get("description",
                                         "Recursive value; contents are preserved as-is.")),
            ])
        if target.startswith("#/definitions/"):
            resolved = doc["definitions"][target.split("/")[-1]]
            source = doc
        else:
            file_name, pointer = target.split("#/definitions/")
            source = schemas[file_name.lstrip("./")]
            resolved = source["definitions"][pointer]
        merged = resolve_refs(copy.deepcopy(resolved), source, schemas, seen + (target,))
        # sibling keys (description, overrides) win over the referenced schema
        for key, value in node.items():
            if key != "$ref":
                merged[key] = resolve_refs(value, doc, schemas, seen)
        return merged

    return OrderedDict((k, resolve_refs(v, doc, schemas, seen)) for k, v in node.items())


def merge_schema(base, overlay):
    """Deep-merge one schema node into another; the overlay narrows the base."""
    for key, value in overlay.items():
        if key == "properties":
            props = base.setdefault("properties", OrderedDict())
            for name, node in value.items():
                if name in props and isinstance(props[name], dict) and isinstance(node, dict):
                    merge_schema(props[name], node)
                else:
                    props[name] = node
        elif key == "required":
            base["required"] = list(dict.fromkeys(base.get("required", []) + value))
        else:
            base[key] = value
    return base


def flatten_allof(node):
    """
    Fold allOf branches that carry structure into the parent node.

    A structural schema may not set type/properties inside a logical junctor, so
    `allOf: [<full object>, <narrowing>]` has to become one merged object. Branches
    that are pure if/then conditionals are left alone for the CEL pass.
    """
    if isinstance(node, list):
        return [flatten_allof(v) for v in node]
    if not isinstance(node, dict):
        return node

    node = OrderedDict((k, flatten_allof(v)) for k, v in node.items())

    branches = node.get("allOf")
    if isinstance(branches, list):
        structural_branches = [b for b in branches
                               if isinstance(b, dict) and "if" not in b
                               and any(k in b for k in ("type", "properties", "required",
                                                        "enum", "items"))]
        if structural_branches:
            for branch in structural_branches:
                merge_schema(node, branch)
            remaining = [b for b in branches if b not in structural_branches]
            if remaining:
                node["allOf"] = remaining
            else:
                del node["allOf"]

    return node


def cel_from_conditional(block):
    """Turn an if/then 'property equals X implies Y is required' pair into a CEL rule."""
    cond, then = block.get("if"), block.get("then")
    if not isinstance(cond, dict) or not isinstance(then, dict):
        return None

    discriminators = []
    for prop, constraint in (cond.get("properties") or {}).items():
        if "const" in constraint:
            discriminators.append((prop, constraint["const"]))
        elif isinstance(constraint.get("enum"), list) and len(constraint["enum"]) == 1:
            discriminators.append((prop, constraint["enum"][0]))
    required = then.get("required")
    if not discriminators or not required:
        return None

    guard = " && ".join(f"self.{p} == '{v}'" for p, v in discriminators)
    need = " && ".join(f"has(self.{r})" for r in required)
    fields = ", ".join(required)
    values = ", ".join(f"{p}={v}" for p, v in discriminators)
    return {"rule": f"!({guard}) || ({need})",
            "message": f"{fields} required when {values}"}


def structural(node, rules_sink=None):
    """Rewrite a resolved JSON Schema into a Kubernetes structural schema."""
    if isinstance(node, list):
        return [structural(v) for v in node]
    if not isinstance(node, dict):
        return node

    out = OrderedDict()
    local_rules = []

    for key, value in node.items():
        if key in ("$schema", "$id", "definitions", "examples", "title", "version",
                   "propertyNames", "patternProperties", "dependencies", "not"):
            continue
        if key.startswith("x-agentbox"):
            if key == "x-agentbox-rules":
                local_rules.extend(value)
            continue
        if key == "const":
            out["enum"] = [value]
            continue
        if key == "format":
            if value in ALLOWED_FORMATS:
                out["format"] = value
            continue
        if key in ("if", "then", "else"):
            continue
        if key in ("allOf", "anyOf", "oneOf"):
            # branches carrying type/properties are illegal inside junctors; lift
            # what we can into CEL and drop the rest
            for branch in value if isinstance(value, list) else [value]:
                rule = cel_from_conditional(branch)
                if rule:
                    local_rules.append(rule)
            continue
        if key == "properties":
            out["properties"] = OrderedDict((k, structural(v)) for k, v in value.items())
            continue
        out[key] = structural(value)

    # allOf on the node itself may hold if/then pairs
    for branch in node.get("allOf", []) or []:
        rule = cel_from_conditional(branch)
        if rule and rule not in local_rules:
            local_rules.append(rule)

    # properties and additionalProperties are mutually exclusive; pruning is the default
    if "properties" in out and "additionalProperties" in out:
        del out["additionalProperties"]

    # every node needs an explicit type
    if "type" not in out:
        if "enum" in out:
            values = out["enum"]
            out["type"] = "string" if all(isinstance(v, str) for v in values) else "object"
        elif "properties" in out or "additionalProperties" in out:
            out["type"] = "object"
        elif "items" in out:
            out["type"] = "array"
    if isinstance(out.get("type"), list):
        # union types are not structural; keep the object-ish branch open
        out.pop("type")
        out["x-kubernetes-preserve-unknown-fields"] = True

    # free-form object: no properties and no value schema
    if out.get("type") == "object" and "properties" not in out and "additionalProperties" not in out:
        out["x-kubernetes-preserve-unknown-fields"] = True
    if not out:
        out["x-kubernetes-preserve-unknown-fields"] = True

    if local_rules:
        if rules_sink is None:
            out["x-kubernetes-validations"] = local_rules
        else:
            rules_sink.extend(local_rules)

    return out


def build_crd(schema, schemas):
    """Build one CustomResourceDefinition from a CRD schema document."""
    crd_meta = schema["x-agentbox-crd"]
    kind = crd_meta["kind"]
    version = crd_meta["version"]
    plural = crd_meta["plural"]

    resolved = flatten_allof(resolve_refs(copy.deepcopy(schema), schema, schemas))
    spec_schema = structural(resolved["properties"]["spec"])
    status_schema = structural(resolved["properties"]["status"])
    status_schema.setdefault("x-kubernetes-preserve-unknown-fields", True)

    open_api = OrderedDict([
        ("type", "object"),
        ("description", schema.get("description", "")),
        ("required", ["spec"]),
        ("properties", OrderedDict([
            ("apiVersion", {"type": "string"}),
            ("kind", {"type": "string"}),
            ("metadata", {"type": "object"}),
            ("spec", spec_schema),
            ("status", status_schema),
        ])),
    ])

    version_entry = OrderedDict([
        ("name", version),
        ("served", True),
        ("storage", True),
        ("subresources", OrderedDict([("status", {})])),
        ("schema", {"openAPIV3Schema": open_api}),
    ])

    if kind in SCALABLE:
        version_entry["subresources"]["scale"] = OrderedDict([
            ("specReplicasPath", SCALABLE[kind]),
            ("statusReplicasPath", ".status.replicas"),
            ("labelSelectorPath", ".status.selector"),
        ])

    columns = [OrderedDict([("name", n), ("type", t), ("jsonPath", p)])
               for n, t, p in PRINTER_COLUMNS.get(kind, [])]
    columns.append(OrderedDict([("name", "State"), ("type", "string"),
                                ("jsonPath", ".status.state")]))
    columns.append(OrderedDict([("name", "Age"), ("type", "date"),
                                ("jsonPath", ".metadata.creationTimestamp")]))
    version_entry["additionalPrinterColumns"] = columns

    return OrderedDict([
        ("apiVersion", "apiextensions.k8s.io/v1"),
        ("kind", "CustomResourceDefinition"),
        ("metadata", OrderedDict([
            ("name", f"{plural}.{GROUP}"),
            ("labels", OrderedDict([
                ("app.kubernetes.io/part-of", "agentbox"),
                ("app.kubernetes.io/managed-by", "ai-ctl"),
            ])),
        ])),
        ("spec", OrderedDict([
            ("group", GROUP),
            ("scope", crd_meta.get("scope", "Namespaced")),
            ("names", OrderedDict([
                ("kind", kind),
                ("listKind", kind + "List"),
                ("singular", kind.lower()),
                ("plural", plural),
                ("shortNames", crd_meta.get("shortNames", [])),
                ("categories", CATEGORIES),
            ])),
            ("versions", [version_entry]),
        ])),
    ])


def represent_ordereddict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


yaml.add_representer(OrderedDict, represent_ordereddict)


def render(crd, source):
    header = (f"# Generated by tools/generate_crds.py from schemas/{source}\n"
              f"# Do not edit by hand; edit the schema and regenerate.\n")
    return header + yaml.dump(crd, default_flow_style=False, sort_keys=False, width=100)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="fail if crds/ is out of date instead of writing")
    args = parser.parse_args()

    schemas = load_schemas()
    CRD_DIR.mkdir(exist_ok=True)

    stale = []
    written = 0
    for name, schema in schemas.items():
        if "x-agentbox-crd" not in schema:
            continue
        crd = build_crd(schema, schemas)
        target = CRD_DIR / (schema["x-agentbox-crd"]["slug"] + ".yaml")
        content = render(crd, name)

        if args.check:
            if not target.exists() or target.read_text() != content:
                stale.append(target.name)
        else:
            target.write_text(content)
            written += 1

    if args.check:
        if stale:
            print("CRD manifests are out of date: " + ", ".join(sorted(stale)))
            return 1
        print(f"crds/ is up to date ({len(schemas) - 1} CRDs)")
        return 0

    print(f"wrote {written} CRD manifests to {CRD_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
