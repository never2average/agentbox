# Design

Why the CRD set looks the way it does. These are the decisions I would defend in review,
and the ones I would most like to be argued out of.

## 1. Primitives, not a framework

The test I hold every kind to: *would a platform engineer who has never written an agent
still need this object to run someone else's agent safely?*

If yes, it is a CRD. If it only makes sense to the person writing agent code, it belongs
in their image. This is why there is a `Gateway` but no prompt template, a `ToolServer`
but no tool-calling loop, a `Guardrail` but no reasoning policy.

## 2. No agent DSL — the image is the boundary

An earlier version of `HarnessRuntime` carried the whole agent: a graph of nodes, prompts
with variables, pre/post execution hooks, usage limits per node. It was about 30 KB of
schema and it was wrong.

The moment agent logic lives in YAML, the platform team owns agent logic. Every prompt
change becomes a platform pull request. Every new framework version becomes a schema
migration. The platform becomes the bottleneck, which is the exact failure this project
exists to prevent.

So `HarnessRuntime` is eight fields:

```yaml
spec:
  runtimeKind: server
  code: {image, entrypoint, args}
  replicas: 2
  compute: {cpu, gpu, storage}
  env: {}
  endpoints: [{name, interface, port, path}]
  schedule: {cronExpression, timezone}
  health: {type, path, port, initialDelaySeconds, periodSeconds}
```

Run this image, this big, on these ports, and tell me when it is healthy. Nothing else.

## 3. The CRDs do not reference each other

A `HarnessRuntime` does not declare the models, gateways, tool servers or datasets it
uses. I tried it the other way — every kind carrying typed references to its neighbours —
and it produced a dependency graph that was wrong in principle and painful in practice.

Wrong in principle, because a tool server is used *by* an agent at runtime; it is not a
component the agent's spec assembles. The relationship is discovery, not composition —
the same way a web service finds its database.

Painful in practice, because references make objects undeletable, unreviewable in
isolation, and prone to dangling. A spec that names five other objects cannot be read on
its own or moved on its own.

The single exception is an AutoScaler's `scaleTargetRef`. An autoscaler with no target
does nothing at all, so ownership is intrinsic there.

Where a name is genuinely needed — a guardrail condition reading a metric, an LLM judge
picking a model — it is a plain name string, not a typed pointer. The platform resolves
it; the schema does not enforce a graph.

## 4. Delegate everything Kubernetes already owns

Kinds I deleted rather than kept:

| Removed | Because |
|---|---|
| Node pools / hardware | Karpenter and the cluster autoscaler own capacity |
| Notification channels | Alertmanager, and every company already has a routing setup |
| Escalation rules | Same — this is on-call tooling, not AI infrastructure |
| Generic background workers | Job and CronJob exist |
| Generic runtimes | Deployment exists; `HarnessRuntime` is the AI-specific case |

Every one of these was tempting because it was easy to write. None of them would have been
easy to *own*. A platform that forks capacity management or alert routing has taken on a
second job it will do worse than the incumbent.

## 5. Kubernetes conventions, not our own

- **camelCase fields**, because that is what every other CRD does and what `kubectl
  explain`, JSONPath and CEL expect.
- **`apiVersion` / `kind` / `metadata` / `spec` / `status`** on every object, with
  `metadata.name` as the only name. No `id`, no `modelId`, no `runtimeId` — a resource
  already has a name.
- **Status is written by the platform**, never by the author. It carries `state`,
  `conditions`, `observedGeneration`, and for the scalable kinds `replicas`,
  `readyReplicas` and `selector`.
- **Structural schemas**, so the API server actually validates and prunes. That meant
  inlining every `$ref`, folding `const` into `enum`, and cutting the one recursive type
  (OpenTelemetry values) with `x-kubernetes-preserve-unknown-fields`.
- **CEL for what schemas cannot say.** 37 `x-kubernetes-validations` rules carry the
  conditionals — a cron harness needs a schedule, a server harness needs an endpoint, tool
  names must be unique within a server, exactly one dataset connector variant must be set.

## 6. Discriminated unions over `oneOf`

`Dataset.config` used to be a `oneOf` across nine connector shapes. Structural schemas
cannot express that, so it would have degraded into an unvalidated blob.

Instead the config is a struct with one optional field per connector — `httpPoll`,
`kafka`, `s3`, `database`, … — plus CEL rules requiring exactly one, matching
`spec.type`. This is the same pattern Kubernetes uses for volume sources, and it keeps
full validation on every branch. `Evaluator.dataset` works the same way.

While doing that I cut `Dataset.type` from eighteen values to the nine that actually have
a config schema. The other nine were aspirational, and an enum value with nothing behind
it is a lie the API tells its users.

## 7. Two planes, because two rhythms

The serving plane changes on request timescales — traffic arrives, replicas move, budgets
burn down. The training plane changes on batch timescales — a nightly fine-tune, a weekly
evaluation, a dataset refresh.

Splitting them is not just documentation. It tells you which objects a controller must
reconcile in seconds and which it can reconcile in minutes, and it maps cleanly onto who
gets paged.

## 8. Version discipline

`v1beta1` with a hard break from the earlier alpha shape — no deprecated aliases, no
silent field mapping. Old field names are rejected at validation rather than quietly
accepted, because a shim that half-works is worse than an error message.

From here, field renames need a version bump.

## Open questions

Things I have not settled, and would like input on:

- **Should the autoscalers be one kind with a target selector**, rather than three kinds
  that differ only in the kind they point at and one specialised block?
- **Is `Recipe` earning its place?** It overlaps with `TrainLoop` and with plain
  pipeline tools. It is the kind I am most likely to cut.
- **Does `AgentIdP` belong here at all**, or should it be a thin mapping onto whatever the
  cluster already uses for workload identity — SPIFFE, IRSA, Workload Identity?
- **Where does memory live?** Vector stores are currently `Dataset` connectors, which
  feels like an undersized answer for something so central to how agents behave.
