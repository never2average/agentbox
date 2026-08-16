# Controller

The CRDs describe what should exist. The controller makes it exist.

```bash
kubectl apply -k crds/      # the API
kubectl apply -k deploy/    # the controller
kubectl -n agentbox-system logs -l app.kubernetes.io/name=agentbox-controller -f
```

```
14:50:31 INFO  agentbox harnessruntimes    agents/support-agent   -> active
14:50:31 INFO  agentbox toolservers        agents/text-tools      -> active
14:50:31 INFO  agentbox aimeters           agents/tenant-spend    -> active
14:51:22 INFO  agentbox scaled HarnessRuntime/support-agent from 2 to 10
```

## What each kind reconciles into

| Kind | What the controller builds | Status it reports |
|---|---|---|
| `HarnessRuntime` | Deployment + Service, or Job, or CronJob, by `spec.runtimeKind` | replicas, readyReplicas, selector, endpoints |
| `ToolServer` | Deployment + Service, plus a **tool catalog ConfigMap** | tools, catalogConfigMap, address, replicas |
| `Model` | Deployment + Service when `spec.serving.image` is set; registry entry otherwise | address, replicas, readyReplicas |
| `Gateway` | Routing ConfigMap, plus a Deployment + Service when `spec.serving` is set | configMap, upstream, address |
| `TrainLoop` | CronJob when scheduled, Job otherwise | succeeded, failed, active, schedule |
| `Evaluator` | Suite ConfigMap; a Job per requested run | suiteConfigMap, caseCount, lastRunId |
| `ModelAutoScaler` | Patches the target's `spec.replicas` | currentReplicas, desiredReplicas, metrics, lastScaleTime |
| `HarnessSwarmAutoScaler` | Same, for a HarnessRuntime | same |
| `ToolServerAutoScaler` | Same, for a ToolServer | same |
| `AgentIdP` | ServiceAccount + Role + RoleBinding | serviceAccount, role, guardrails |
| `AIMetric` | Prometheus recording-rule ConfigMap | metricName, currentValue, ruleConfigMap |
| `AIMeter` | Computes usage, cost (flat, per-unit, tiered) and budget position, broken down by attribution dimensions | currentUsage, currentCost, attributedUsage, budgetUsedPercent, budgetExceeded |
| `Guardrail` | Evaluates conditions, emits events, honours `cooldownSeconds` and `suppressForSeconds` | triggered, suppressed, inCooldown, observations |
| `Tracer` | OpenTelemetry Collector config ConfigMap | collectorConfigMap, resourceAttributes |
| `Dataset` | Connector config ConfigMap | connectorConfigMap, address, checkpointing |
| `Recipe` | Topologically sorts stages, publishes the plan | resolvedOrder, stageCount, planConfigMap |

Every child carries an owner reference back to its AgentBox object. Delete the object and
Kubernetes garbage-collects everything it made — no finalizers, nothing to get stuck.

## Credentials

Nothing that runs reads a credential out of a spec. Every kind that runs a pod takes
`spec.secrets` — a map of environment variable name to `{name, key}` in a Secret — and the
controller turns those into `valueFrom.secretKeyRef` on the container.

`Gateway.litellmParams.apiKeySecretRef` is the same idea for the upstream provider key: the
published config gets `os.environ/AGENTBOX_GATEWAY_API_KEY`, which is LiteLLM's own syntax,
and the value is injected into the pod from the Secret.

A `Dataset` does not run anything, so its connector config is published as a ConfigMap for
consumers to read. Anything that looks like a credential is **redacted** on the way out,
and `status.redactedFields` names what was stripped. Use the `*SecretRef` variants and the
reference is published instead, for the consumer to resolve itself.

The inline fields still exist and still validate, because removing them would be a breaking
change — but they are marked deprecated, and the controller will not copy their values
anywhere.

## Where metric values come from

Autoscalers, meters and guardrails all need numbers. Two sources, tried in order:

1. **Prometheus**, when `AGENTBOX_PROMETHEUS_URL` is set on the controller. Each `AIMetric`
   name is queried directly.
2. **The `agentbox-metrics` ConfigMap** in the resource's namespace — a map of metric name
   to value.

The second exists because a platform team adopting this rarely has the metrics pipeline
ready on day one, and because pinning a value by hand is the fastest way to see what a
guardrail or autoscaler will do before you trust it with real traffic:

```bash
kubectl -n agents create configmap agentbox-metrics \
  --from-literal=pending-agent-sessions=25 \
  --from-literal=gateway-tokens=2000000000 \
  --from-literal=gateway-tokens.tenant_id.acme=1500000000
```

A key of the form `metric.dimension.value` supplies the per-dimension breakdown an
`AIMeter` attributes usage with; Prometheus supplies the same thing via
`sum by (dimension) (metric)`.

Resource metrics (`cpu`, `memory`) fall back to `metrics.k8s.io` when neither source has a
value.

## How scaling decides

The HorizontalPodAutoscaler formula, deliberately:

```
desired = ceil(currentReplicas × observed / target)
```

with a **10% tolerance band** (`spec.tolerance`) so a metric sitting near its target does
not cause churn. `stabilizationWindowSeconds` sets a quiet period after each change, and
`policies` cap how far one step may move — `pods` for an absolute step, `percent` for a
relative one, `periodSeconds` for the minimum gap between steps, and
`selectPolicy: disabled` to block a direction entirely. When several metrics disagree, the
highest demand wins — the same rule HPA uses.

`scaleToZero.enabled` lets the floor drop to 0; otherwise `bounds.minReplicas` is clamped to
at least 1. At zero replicas there is no per-replica average to reason about, so any demand
at all brings one replica back and normal scaling resumes from there — subject to the same
scale-up stabilization window.

Autoscaling is deliberately *declarative all the way down*: the autoscaler patches the
target's `spec.replicas`, and the target's own reconciler moves the Deployment. You can
always see the decision in the object rather than only in the workload.

## How enforcement reaches the data plane

The controller decides; the gateway acts. Every Gateway's generated config carries an
`enforcement` list — the guardrails that have tripped and scope this gateway, plus the
meters whose budget is spent with an `onExceed` other than `alert`:

```json
"enforcement": [
  {"source": "demo-spend", "kind": "AIMeter", "effect": "throttle",
   "message": "budget of 10 exceeded"}
]
```

That config is mounted into the gateway's own pod at `/etc/agentbox/config.json`, so a
gateway reads it without talking to the Kubernetes API. `spec.files` mounts any other
ConfigMap or Secret the same way, into a harness, tool server or model.

Reconcile order matters here: the kinds that *decide* — AIMetric, AIMeter, Guardrail — run
before the kinds that *act*, so a breach reaches a gateway in the same pass rather than the
next one.

## What it does not do

- **Force a gateway to obey.** It publishes the verdict; a gateway that ignores its config
  enforces nothing. The demo gateway in `examples/` shows the contract: read
  `enforcement`, return 429.
- **Move data.** A `Dataset` publishes its connector config; nothing in the controller reads
  from Kafka on your behalf.
- **Enforce guardrail effects.** See above; the verdict is recorded, the action is not taken.

## Running more than one

`--leader-elect` takes a `coordination.k8s.io` Lease named `agentbox-controller`. Only the
holder reconciles; a second replica waits, and takes over within about 15 seconds if the
holder stops renewing. `deploy/` runs two replicas with the flag set.

## Running it

In-cluster is `kubectl apply -k deploy/`. Against a cluster from your laptop:

```bash
python -m controller.main --kubeconfig ~/.kube/config --namespace agents
python -m controller.main --kubeconfig ~/.kube/config --once     # reconcile once and exit
```

`--once` is what the end-to-end suite uses, and it is the honest way to see what the
controller would do before you leave it running.

Useful flags: `--prometheus-url`, `--resync-seconds` (default 60), `--workers` (default 4),
`--log-level DEBUG`.
