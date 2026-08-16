# Changelog

## v0.1.0 — unreleased

The first release: 16 CRDs, a controller that reconciles all of them, and a demo
that proves the loop with real processes.

### The CRD set

`ai.agentbox.io/v1beta1`, sixteen kinds across a serving plane and a training plane:
`Model`, `ModelAutoScaler`, `HarnessRuntime`, `HarnessSwarmAutoScaler`, `AgentIdP`,
`ToolServer`, `ToolServerAutoScaler`, `Gateway`, `AIMetric`, `AIMeter`, `TrainLoop`,
`Dataset`, `Evaluator`, `Guardrail`, `Tracer`, `Recipe`.

- Structural schemas with 40 CEL validation rules, so bad specs are rejected by the API
  server rather than discovered at runtime
- Status subresource on every kind; scale subresource on `HarnessRuntime`, `ToolServer`
  and `Model`
- Printer columns, short names, and an `agentbox` category
- Generated from `schemas/` — `crds/`, `docs/crd-reference.md` and `install.yaml` all
  come from one source of truth, with `--check` modes to catch drift

### The controller

- Reconciles all sixteen kinds; every child carries an owner reference, so deleting an
  object garbage-collects its workloads
- Autoscaling on the HorizontalPodAutoscaler formula, with a tolerance band,
  stabilization windows, rate-limit policies and scale-to-zero (including waking again
  when demand returns)
- Metering with flat, per-unit and tiered pricing, budgets, threshold and breach events,
  and per-dimension attribution
- Guardrails with every comparison operator, `all`/`any` semantics, cooldown and
  suppression
- Enforcement reaches the data plane: a tripped guardrail or a spent budget lands in the
  Gateway's config, and the gateway refuses traffic
- Leader election over a Lease, so more than one replica is safe
- Metric values from Prometheus, or from an `agentbox-metrics` ConfigMap for clusters
  that do not have a metrics stack yet

### Installing

```bash
kubectl apply -f https://github.com/never2average/agentbox/releases/download/v0.1.0/install.yaml
```

### Also included

- `examples/demo.yaml` — an agent, gateway, model and tool server that actually run and
  actually talk to each other, with no registry required
- `k8s_modules/` — a Python CRUD layer for clusters where CRDs cannot be installed
- `tests/e2e_test.py` — 244 assertions against a live API server
- `docs/test-cases.md` — 186 catalogued cases, marked automated or not

### Known limitations

- The controller publishes enforcement decisions; a gateway that ignores its config
  enforces nothing
- No admission webhook — validation is the CRD schema and its CEL rules
- No real-model integration tested: the demo's model and gateway are small stand-ins,
  not vLLM and LiteLLM
- Tested on kind only, on one Kubernetes version
