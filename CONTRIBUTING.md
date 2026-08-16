# Contributing

Thanks for looking. The most valuable contributions here are arguments about the API, not
just code — this is a vocabulary, and vocabularies are worth arguing over before they
harden.

## The one rule

**`schemas/` is the source of truth.** `crds/` and `docs/crd-reference.md` are generated
from it. Never hand-edit either:

```bash
python tools/generate_crds.py        # schemas/ -> crds/
python tools/generate_crd_docs.py    # schemas/ -> docs/crd-reference.md
```

Both take `--check`, which fails instead of writing. CI runs them that way, so a PR that
edits a schema without regenerating will be caught.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python test_crud_basic.py
```

The tests need no cluster.

## Changing a CRD

1. Edit `schemas/<group>-schema.json`.
2. Keep the envelope: `apiVersion`, `kind`, `metadata`, `spec`, `status`. Fields go in
   `spec`; nothing goes at the top level.
3. Use camelCase field names, and camelCase enum values.
4. Give every new field a `description` — it becomes `kubectl explain` output and a row in
   the generated reference.
5. Reference shared definitions from `common-schema.json` rather than redefining
   (`objectMeta`, `resourceStatus`, `computeResources`, `replicaBounds`, `scalingMetric`,
   `scalingBehavior`, `dnsName`).
6. Regenerate, run the tests, commit the generated files with your change.

### Things the generator cannot carry into a CRD

Structural schemas are a subset of JSON Schema. These get dropped or transformed:

| In the schema | What happens |
|---|---|
| `$ref` | Inlined |
| `const` | Folded into single-value `enum` |
| `additionalProperties` next to `properties` | Dropped; unknown fields are pruned anyway |
| `if` / `then` | Lifted into a CEL rule when it is a "X implies Y is required" pair |
| `oneOf` / `anyOf` with typed branches | **Dropped.** Model discriminated unions instead — see `Dataset.config` |
| Recursive `$ref` | Cut with `x-kubernetes-preserve-unknown-fields` |

For validation a schema cannot express, add `x-agentbox-rules` to the node — a list of
`{rule, message}` CEL entries. They land in `x-kubernetes-validations`:

```json
"x-agentbox-rules": [
  {"rule": "self.runtimeKind != 'cron' || has(self.schedule)",
   "message": "schedule is required when runtimeKind is cron"}
]
```

## Adding a CRD

Make the case first, in an issue. The bar is the one in [docs/design.md](docs/design.md):
*would a platform engineer who has never written an agent need this object to run someone
else's agent safely?* If an existing Kubernetes CRD covers it, the answer is no.

If it clears the bar:

1. `schemas/<group>-schema.json` with `x-agentbox-crd` (`group`, `version`, `kind`,
   `slug`, `plural`, `scope`, `shortNames`).
2. Register it in `k8s_modules/registry.py` — `CRD_KINDS`, plus `_WORKLOAD_MANAGERS` if it
   creates Kubernetes workloads.
3. Add printer columns and a one-liner to `tools/generate_crds.py` and
   `tools/generate_crd_docs.py`.
4. Write a reconciler in `controller/reconcilers/` and register it in
   `controller/manager.py`. A reconciler takes `(ctx, resource)` and returns the status to
   write; build children with `controller.children` so they carry owner references.
5. Add the group to `PLANES` in the docs generator.
6. Regenerate, and add it to `crds/kustomization.yaml`.

Removing a CRD is just as welcome, and needs the same kind of argument.

## Removing or renaming a field

`v1beta1` means field names are stable. Renames need a new API version and a conversion
story — not an alias. If you need to deprecate something, mark it in the description and
open an issue for the version bump.

## Style

The Python here is deliberately plain: type hints, docstrings with Args/Returns, imports
at the top, no nested function definitions. Match what is around you.

## Tests

`test_crud_basic.py` covers imports, the store utilities, registry/schema parity, name
extraction and validation. If you add a kind, the parity test will fail until the schema
and registry agree — that is the point.

### End-to-end

`tests/e2e_test.py` runs the whole loop against a real API server: installs the CRDs,
applies every reference example, checks that 16 bad specs are rejected, exercises the scale
and status subresources, drives the Python managers through CRUD, and runs the controller —
asserting that it builds every child with the right owner, scales targets by the HPA
formula, prices usage against budgets and trips guardrails. 252 assertions.

```bash
kind create cluster --name agentbox-e2e --kubeconfig /tmp/agentbox-kubeconfig
python tests/e2e_test.py \
  --context kind-agentbox-e2e \
  --kubeconfig /tmp/agentbox-kubeconfig
```

It creates a dedicated namespace and removes it, and the CRDs, at the end. `--kubeconfig`
is required — the suite refuses to guess, so it can never wander onto a cluster you did
not name. CI runs it on every PR against a kind cluster.

`checks that 16 bad specs are rejected` is 26 now, and the count moves — what matters is
the catalogue in [docs/test-cases.md](docs/test-cases.md), which lists every case worth
running and marks what is automated. If you are looking for a first contribution, pick an
unmarked case from there; the priorities are listed at the bottom of that page.

Before opening a PR:

```bash
python test_crud_basic.py
python tools/generate_crds.py --check
python tools/generate_crd_docs.py --check
```
