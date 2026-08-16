# Quickstart

Fifteen minutes from an empty cluster to a running agent with a tool server, a gateway and
an autoscaler.

## Prerequisites

- A Kubernetes cluster (1.25+, for CEL validation rules) and `kubectl` access
- Python 3.8+ if you want the CRUD layer
- Permission to install CRDs — if you do not have it, skip to
  [Without CRDs](#without-crds)

## 1. Install

```bash
# everything, from a release
kubectl apply -f https://github.com/never2average/agentbox/releases/latest/download/install.yaml

# or with Helm
helm install agentbox oci://ghcr.io/never2average/charts/agentbox --namespace agentbox-system --create-namespace

# or from a clone
kubectl apply -k crds/ && kubectl apply -k deploy/
```

Check what landed:

```bash
kubectl get crds -l app.kubernetes.io/part-of=agentbox
kubectl api-resources --categories=agentbox
```

```
NAME                      SHORTNAMES   APIVERSION                     NAMESPACED   KIND
agentidps                 idp          ai.agentbox.io/v1beta1         true         AgentIdP
aimeters                  aime         ai.agentbox.io/v1beta1         true         AIMeter
aimetrics                 aim          ai.agentbox.io/v1beta1         true         AIMetric
datasets                  ds           ai.agentbox.io/v1beta1         true         Dataset
...
```

## 2. Declare a gateway

Nothing about the agent should know which provider you are using. Put that decision here:

```yaml
apiVersion: ai.agentbox.io/v1beta1
kind: Gateway
metadata:
  name: openai-compatible
spec:
  modelName: llama-3-70b-instruct
  litellmParams:
    model: openai/llama-3-70b-instruct
    apiBase: http://vllm.models.svc:8000/v1
    rpm: 10000
    tpm: 1000000
    timeout: 600
    # credentials by reference, never inline
    apiKeySecretRef:
      name: provider-credentials
      key: apiKey
  modelInfo:
    id: llama-3-70b-instruct
    mode: chat
    supportsFunctionCalling: true
```

```bash
kubectl create secret generic provider-credentials --from-literal=apiKey=sk-...
```

The controller injects that key into the gateway pod and renders
`os.environ/AGENTBOX_GATEWAY_API_KEY` into the config it publishes, so the value
never lands in a ConfigMap, in etcd as part of this object, or in the repository
that holds it.

Swapping to Bedrock later is a change to this object, not to any image.

## 3. Run an agent

Your image, your framework. The harness only describes how to run it:

```yaml
apiVersion: ai.agentbox.io/v1beta1
kind: HarnessRuntime
metadata:
  name: support-agent
  labels:
    app.kubernetes.io/version: "1.4.0"
spec:
  runtimeKind: server
  code:
    image: acme/support-agent:1.4.0
    entrypoint: /app/serve
  replicas: 2
  compute:
    cpu:
      cores: 4
      memoryMb: 8192
  env:
    AGENT_PROFILE: support
  endpoints:
    - name: api
      interface: http
      port: 8080
      path: /api
  health:
    type: http
    path: /healthz
    port: 8080
```

```bash
kubectl apply -f support-agent.yaml
kubectl get harnessruntime support-agent
```

```
NAME            KIND     IMAGE                      REPLICAS   READY   STATE    AGE
support-agent   server   acme/support-agent:1.4.0   2          2       active   30s
```

`runtimeKind` decides the workload: `server` and `worker` become a Deployment (plus a
Service when endpoints are declared), `batch` becomes a Job, `cron` becomes a CronJob.
A cron harness must carry a `schedule` — the API server enforces that with a CEL rule
rather than letting you find out at runtime.

## 4. Serve some tools

A tool server is an image that serves tools and publishes their contracts, so callers know
what they are calling:

```yaml
apiVersion: ai.agentbox.io/v1beta1
kind: ToolServer
metadata:
  name: text-tools
spec:
  code:
    image: acme/text-tools:2.1.0
  endpoint:
    interface: http
    port: 8080
    basePath: /tools
  replicas: 2
  tools:
    - name: summarize-text
      description: Summarize input text into a short abstract
      path: /summarize
      parameters:
        type: object
        properties:
          text:
            type: string
        required: [text]
      returns:
        type: object
```

The agent finds it the ordinary way — the Service DNS name — not through a reference in
its own spec. That is deliberate; see [design.md](design.md#3-the-crds-do-not-reference-each-other).

## 5. Scale it

Both workload kinds expose the scale subresource:

```bash
kubectl scale harnessruntime/support-agent --replicas=5
```

For load-driven scaling, declare the signal:

```yaml
apiVersion: ai.agentbox.io/v1beta1
kind: HarnessSwarmAutoScaler
metadata:
  name: support-agent-swarm
spec:
  scaleTargetRef:
    kind: HarnessRuntime
    name: support-agent
  bounds:
    minReplicas: 2
    maxReplicas: 50
  metrics:
    - type: aiMetric
      metric: pending-agent-sessions
      target:
        metricType: averageValue
        value: 5
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
    scaleToZero:
      enabled: true
      idleSeconds: 600
  sessionAffinity:
    enabled: true
    maxSessionsPerHarness: 8
    drainTimeoutSeconds: 300
```

The controller reads `pending-agent-sessions`, applies the HorizontalPodAutoscaler formula
with a 10% tolerance band, and patches `spec.replicas` on the harness — which its own
reconciler then rolls out to the Deployment. Watch the decision:

```bash
kubectl get harnessswarmautoscaler support-agent-swarm -o jsonpath='{.status}' | jq
```

Before you have a metrics pipeline, pin the value by hand:

```bash
kubectl create configmap agentbox-metrics --from-literal=pending-agent-sessions=25
```

## 6. Put a budget on it

```yaml
apiVersion: ai.agentbox.io/v1beta1
kind: AIMeter
metadata:
  name: tenant-token-spend
spec:
  usage:
    unit: totalTokens
    source:
      metric: gateway-tokens
      statistic: Sum
    subjects: [openai-compatible]
  attribution:
    dimensions: [tenant_id]
  window:
    type: billingPeriod
    period: monthly
  pricing:
    currency: USD
    model: perUnit
    unitPrice: 0.6
    perUnits: 1000000
  budget:
    limit: 5000
    limitType: cost
    onExceed: throttle
```

This is the object I most wanted to exist. "What did agents cost us last month, by tenant"
should be a `kubectl get`, not a data pull — and it is:

```bash
kubectl get aimeter tenant-token-spend \
  -o jsonpath='{.status.currentUsage} tokens = {.status.currentCost} USD ({.status.budgetUsedPercent}% of budget)'
```

The controller prices flat, per-unit and tiered models, emits a `BudgetThreshold` event at
each alert percentage and `BudgetExceeded` when the limit is passed.

## Without CRDs

Plenty of regulated clusters will not let you install CRDs. The Python layer stores the
same specs in ConfigMaps and Secrets in the `agentbox-system` namespace, validated against
the same schemas:

```python
from k8s_modules.registry import get_manager

mgr = get_manager("harness-runtime", "/path/to/kubeconfig")
mgr.create({
    "kind": "HarnessRuntime",
    "metadata": {"name": "support-agent"},
    "spec": {
        "runtimeKind": "server",
        "code": {"image": "acme/support-agent:1.4.0"},
        "endpoints": [{"name": "api", "port": 8080}],
    },
})
```

Secret-looking fields (`apiKey`, `password`, `token`, `*_key`) are moved into a Secret
automatically, and `get(..., includeSecrets=True)` merges them back for the callers that
need them. Full guide: [python-api.md](python-api.md).

## Next

- [CRD reference](crd-reference.md) — every field of every kind
- [Architecture](architecture.md) — how this maps onto a real cluster
- [Controller](controller.md) — what each kind reconciles into
- [Design](design.md) — why the set looks like this
