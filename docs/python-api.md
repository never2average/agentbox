# Python API

Kubernetes-backed CRUD managers for the AgentBox CRDs, for callers that want a library
rather than `kubectl` — and for clusters where you cannot install CRDs at all, since the
same specs are persisted to ConfigMaps and Secrets.

If you just want to apply YAML, start with the [quickstart](quickstart.md) instead.

## Overview

This implementation provides CRD-like behavior for the AgentBox CRD set without requiring custom resource definitions to be installed in the cluster. Resources are stored in ConfigMaps and Secrets in the `agentbox-system` namespace, with appropriate Kubernetes workloads (Deployments, Services, Jobs, CronJobs) created as needed.

Anything a stock Kubernetes CRD already covers - node pools, alert routing/notification/escalation, generic workloads - is deliberately **not** an AgentBox CRD. AgentBox only defines what is AI-native.

### The CRD envelope

Every AgentBox resource uses the same Kubernetes-style envelope:

```json
{
  "apiVersion": "ai.agentbox.io/v1beta1",
  "kind": "HarnessRuntime",
  "metadata": { "name": "api-harness" },
  "spec": { "...": "kind-specific configuration" },
  "status": { "state": "active" }
}
```

- Field names are camelCase, matching Kubernetes API convention.
- `apiVersion` and `kind` are filled in automatically when omitted, and validated against the CRD's schema when present.
- `metadata.name` is the resource name (DNS-1123) and is required.
- `spec` holds all kind-specific configuration.
- `status` is synthesized from the backing workloads - never send it.

**The CRDs are independent primitives.** A HarnessRuntime does not declare the models, gateways or tool servers it uses — an agent picks those up at runtime by ordinary service discovery. The only reference that exists in a spec is an AutoScaler's `scale_target_ref`, because an autoscaler with no target is meaningless.

## Architecture

### Storage Strategy

- **ConfigMaps**: Store public resource specifications
- **Secrets**: Store sensitive fields (auto-detected or explicitly specified)
- **Namespace**: Fixed `agentbox-system` namespace
- **Labels**: All resources tagged with:
  - `app.kubernetes.io/part-of=agentbox`
  - `agentbox.io/managed-by=ai-ctl`
  - `agentbox.io/resource-group=<group>`
  - `agentbox.io/resource-name=<name>`

### The CRD set

Serving plane:

| Resource Group | Kind | Kubernetes Workload | Schema |
|----------------|------|---------------------|--------|
| `model` | `Model` | None | `../schemas/model-schema.json` |
| `model-autoscaler` | `ModelAutoScaler` | None | `../schemas/model-autoscaler-schema.json` |
| `harness-runtime` | `HarnessRuntime` | Deployment + Service / Job / CronJob | `../schemas/harness-runtime-schema.json` |
| `harness-swarm-autoscaler` | `HarnessSwarmAutoScaler` | None | `../schemas/harness-swarm-autoscaler-schema.json` |
| `agent-idp` | `AgentIdP` | None | `../schemas/agent-idp-schema.json` |
| `tool-server` | `ToolServer` | Deployment + Service | `../schemas/tool-server-schema.json` |
| `tool-server-autoscaler` | `ToolServerAutoScaler` | None | `../schemas/tool-server-autoscaler-schema.json` |
| `gateway` | `Gateway` | None | `../schemas/gateway-schema.json` |
| `ai-metric` | `AIMetric` | None | `../schemas/ai-metric-schema.json` |
| `ai-meter` | `AIMeter` | None | `../schemas/ai-meter-schema.json` |

Training plane:

| Resource Group | Kind | Kubernetes Workload | Schema |
|----------------|------|---------------------|--------|
| `train-loop` | `TrainLoop` | Job / CronJob | `../schemas/train-loop-schema.json` |
| `dataset` | `Dataset` | None | `../schemas/dataset-schema.json` |
| `evaluator` | `Evaluator` | None | `../schemas/evaluator-schema.json` |
| `guardrail` | `Guardrail` | None | `../schemas/guardrail-schema.json` |
| `tracer` | `Tracer` | None | `../schemas/tracer-schema.json` |
| `recipe` | `Recipe` | None | `../schemas/recipe-schema.json` |

Each row maps to `../crds/<group>.yaml`, an `apiextensions.k8s.io/v1` CustomResourceDefinition with a
status subresource; `HarnessRuntime` and `ToolServer` also carry a scale subresource, so
`kubectl scale harnessruntime/api-harness --replicas=5` works.

`schemas/common-schema.json` holds the shared definitions (envelope, `object_ref`, autoscaler bounds/metrics/behavior, compute) that every CRD reuses.

A `HarnessRuntime` runs the developer's own image; the agent's graph, prompts and hooks live in that image, not in the CRD. The spec only covers what the platform must know to run it: the image, its compute, its endpoints and its health.

A `ToolServer` is likewise a workload — an image serving one or more tools over HTTP or gRPC — that publishes its tool contracts so callers can discover them.

`HarnessRuntime` maps to a workload by `spec.runtimeKind`: `server`/`worker` -> Deployment (+ Service when endpoints are declared), `batch` -> Job, `cron` -> CronJob. `TrainLoop` maps to a CronJob when `spec.execution.schedule` is set, otherwise a Job.

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Ensure you have access to a Kubernetes cluster
export KUBECONFIG=/path/to/kubeconfig

# Install the CRDs
kubectl apply -k crds/
```

The managers work with or without the CRDs installed: they persist specs to
ConfigMaps and Secrets either way. Installing `../crds/` additionally gives you
`kubectl get harnessruntimes`, server-side validation, printer columns and
`kubectl scale` on the workload kinds.

### Generated manifests

`../crds/*.yaml` are generated from `../schemas/*.json` — edit the schema, never the YAML:

```bash
python tools/generate_crds.py           # regenerate crds/
python tools/generate_crds.py --check   # fail if crds/ is stale (for CI)
```

The generator inlines every `$ref`, folds `const` into `enum`, lifts conditionals into
`x-kubernetes-validations` CEL rules, and marks free-form objects
`x-kubernetes-preserve-unknown-fields` — the requirements for a Kubernetes structural schema.

## Usage

### Basic Example

```python
from k8s_modules.registry import get_manager

# Get manager for a CRD resource group
mgr = get_manager("harness-runtime", kubeconfig_path="/path/to/kubeconfig")

# Create a harness runtime
harness = {
    "apiVersion": "ai.agentbox.io/v1beta1",
    "kind": "HarnessRuntime",
    "metadata": {"name": "my-api"},
    "spec": {
        "runtimeKind": "server",
        "compute": {
            "cpu": {"cores": 2, "memoryMb": 2048}
        },
        "code": {
            "image": "agentbox/harness:1.0.0"
        },
        "endpoints": [{
            "name": "api",
            "interface": "http",
            "port": 8080,
            "path": "/api"
        }]
    }
}

created = mgr.create(harness)
print(f"Created: {created['metadata']['name']}")
print(f"Status: {created['status']['state']}")
```

### CRUD Operations

#### Create

```python
# Basic create
resource = mgr.create(spec)

# Create with explicit secret fields
resource = mgr.create(spec, secret_fields=["config.api_key", "config.password"])
```

#### Read

```python
# Get resource (secrets redacted by default)
resource = mgr.get("resource-name")

# Get resource with secrets
resource = mgr.get("resource-name", include_secrets=True)
```

#### Update

```python
# Merge update (partial spec)
updated = mgr.update("resource-name", partial_spec, strategy="merge")

# Replace update (full spec)
updated = mgr.update("resource-name", full_spec, strategy="replace")
```

#### Delete

```python
# Delete resource and associated workloads
mgr.delete("resource-name")
```

#### List

```python
# List all resources in the group
resources = mgr.list()

# List with label selector
resources = mgr.list(selector={"env": "production"})
```

#### Watch

```python
def on_change(event_type, resource):
    print(f"{event_type}: {resource['name']}")

# Watch for changes (blocks until timeout)
mgr.watch(on_change, timeout_seconds=300)
```

### Using the Registry

```python
from k8s_modules.registry import ResourceRegistry, list_resource_groups, get_kind

# List all CRD resource groups
print(list_resource_groups())
# ['agent-idp', 'ai-meter', 'ai-metric', 'dataset', 'evaluator', 'gateway', ...]

print(get_kind("harness-runtime"))
# 'HarnessRuntime'

# Create registry instance
registry = ResourceRegistry(kubeconfig_path="/path/to/kubeconfig")

# Access different managers
harness = registry.get("harness-runtime")
models = registry.get("model")
meters = registry.get("ai-meter")

# Use managers
harness.create(harness_spec)
models.list()
```

### Convenience Functions

```python
from k8s_modules.registry import (
    create_resource,
    get_resource,
    update_resource,
    delete_resource,
    list_resources
)

kubeconfig = "/path/to/kubeconfig"

# Create
resource = create_resource("harness-runtime", spec, kubeconfig)

# Read
resource = get_resource("harness-runtime", "my-api", kubeconfig)

# Update
updated = update_resource("harness-runtime", "my-api", new_spec, kubeconfig)

# Delete
delete_resource("harness-runtime", "my-api", kubeconfig)

# List
all_harnesses = list_resources("harness-runtime", kubeconfig)
```

## Secret Handling

### Automatic Detection

Fields with these patterns are automatically stored in Secrets:

- Fields ending in: `password`, `token`, `secret`, `_key`, `api_key`
- Fields containing: `credentials`

### Explicit Specification

```python
spec = {
    "kind": "Gateway",
    "metadata": {"name": "openai-compatible"},
    "spec": {
        "modelName": "llama-3-70b-instruct",
        "litellmParams": {
            "model": "openai/llama-3-70b-instruct",
            "apiBase": "https://api.example.com/v1",
            "apiKey": "sk-SECRET"
        },
        "modelInfo": {"id": "llama-3-70b-instruct", "mode": "chat"}
    }
}

# Explicitly mark fields as secret using dot notation
mgr.create(spec, secret_fields=["spec.litellm_params.api_key"])
```

### Retrieving Secrets

```python
# Get without secrets (default) - sensitive fields omitted
resource = mgr.get("openai-compatible")
# spec.litellm_params.api_key will not be present

# Get with secrets
resource = mgr.get("openai-compatible", include_secrets=True)
# spec.litellm_params.api_key will be present
```

## HarnessRuntime Examples

### Server Harness (Deployment + Service)

```python
mgr = get_manager("harness-runtime", kubeconfig_path)

harness = {
    "kind": "HarnessRuntime",
    "metadata": {"name": "web-api"},
    "spec": {
        "runtimeKind": "server",
        "compute": {
            "cpu": {"cores": 4, "memoryMb": 4096}
        },
        "code": {
            "image": "acme/support-agent:1.4.0",
            "entrypoint": "/app/server",
            "args": ["--port", "8080"]
        },
        "endpoints": [
            {
                "name": "api",
                "interface": "http",
                "port": 8080,
                "path": "/api/v1"
            }
        ],
        "env": {"AGENT_PROFILE": "support"}
    }
}

created = mgr.create(harness)
# Creates: Deployment + Service in agentbox-system namespace
```

### Batch Harness (Job)

```python
batch = {
    "kind": "HarnessRuntime",
    "metadata": {"name": "data-processor"},
    "spec": {
        "runtimeKind": "batch",
        "code": {
            "image": "processor:latest",
            "entrypoint": "python",
            "args": ["process.py"]
        }
    }
}

created = mgr.create(batch)
# Creates: Job in agentbox-system namespace
```

### Cron Harness (CronJob)

```python
cron = {
    "kind": "HarnessRuntime",
    "metadata": {"name": "daily-cleanup"},
    "spec": {
        "runtimeKind": "cron",
        "code": {
            "image": "cleanup:latest"
        },
        "schedule": {
            "cronExpression": "0 2 * * *",
            "timezone": "UTC"
        }
    }
}

created = mgr.create(cron)
# Creates: CronJob in agentbox-system namespace
```

## TrainLoop Examples

### One-Time Training Run (Job)

```python
mgr = get_manager("train-loop", kubeconfig_path)

train_loop = {
    "kind": "TrainLoop",
    "metadata": {"name": "sft-once"},
    "spec": {
        "type": "training",
        "version": "1.0.0",
        "status": "active",
        "worker": {
            "image": "acme/trainer:1.0.0",
            "env": {"BASE_MODEL": "llama-3-70b-instruct"}
        },
        "execution": {"mode": "continuous", "timeoutSeconds": 7200}
    }
}

created = mgr.create(train_loop)
# Creates: Job in agentbox-system namespace
```

### Scheduled Training Run (CronJob)

```python
nightly = {
    "kind": "TrainLoop",
    "metadata": {"name": "sft-nightly"},
    "spec": {
        "type": "training",
        "version": "1.0.0",
        "status": "active",
        "worker": {
            "image": "acme/trainer:1.0.0"
        },
        "execution": {
            "mode": "scheduled",
            "timeoutSeconds": 7200,
            "schedule": {"type": "cron", "cronExpression": "0 3 * * *"}
        }
    }
}

created = mgr.create(nightly)
# Creates: CronJob in agentbox-system namespace
```

## AutoScaler Examples

Every AutoScaler CRD shares the same shape: a `scale_target_ref` pinned to the kind it
owns, `bounds`, `metrics` and an optional `behavior`.

```python
mgr = get_manager("model-autoscaler", kubeconfig_path)

autoscaler = {
    "kind": "ModelAutoScaler",
    "metadata": {"name": "llama-3-70b-autoscaler"},
    "spec": {
        "scaleTargetRef": {"kind": "Model", "name": "llama-3-70b-instruct"},
        "bounds": {"minReplicas": 1, "maxReplicas": 8},
        "metrics": [
            {
                "type": "aiMetric",
                "metric": "inference-queue-depth",
                "target": {"metricType": "averageValue", "value": 20}
            },
            {
                "type": "resource",
                "resource": "gpu",
                "target": {"metricType": "utilization", "value": 75}
            }
        ],
        "behavior": {"scaleDown": {"stabilizationWindowSeconds": 600}}
    }
}

created = mgr.create(autoscaler)
```

`HarnessSwarmAutoScaler` targets a `HarnessRuntime` (and adds `session_affinity`);
`ToolServerAutoScaler` targets a `ToolServer` (and adds `concurrency`).

## Status Synthesis

Resource status is dynamically synthesized from Kubernetes workloads:

### Deployment Status

```python
resource = mgr.get("my-runtime")
print(resource['status'])
# {
#     'state': 'active',
#     'replicas': 3,
#     'ready_replicas': 3,
#     'available_replicas': 3,
#     'conditions': [...]
# }
```

### Job Status

```python
resource = mgr.get("my-batch-job")
print(resource['status'])
# {
#     'state': 'completed',
#     'succeeded': 1,
#     'failed': 0,
#     'active': 0,
#     'start_time': '2025-11-12T10:00:00Z',
#     'completion_time': '2025-11-12T10:05:00Z'
# }
```

### CronJob Status

```python
resource = mgr.get("my-cronjob")
print(resource['status'])
# {
#     'state': 'active',
#     'active_jobs': 0,
#     'last_schedule_time': '2025-11-12T10:00:00Z',
#     'last_successful_time': '2025-11-12T10:00:30Z',
#     'schedule': '0 * * * *'
# }
```

## Validation

All resources are validated against their JSON schemas in the `../schemas/` directory before creation or update:

```python
# Invalid spec will raise ValueError with details
try:
    mgr.create(invalid_spec)
except ValueError as e:
    print(f"Validation error: {e}")
```

## Name Resolution

The resource name always comes from `metadata.name`; a spec without it is rejected.
Names are sanitized to be DNS-1123 compliant:
- Lowercase
- Alphanumeric and hyphens only
- Max 63 characters

## Advanced Usage

### Custom Manager

```python
from k8s_modules.base_resource import BaseResourceManager

class CustomManager(BaseResourceManager):
    @property
    def resource_group(self) -> str:
        return "custom"
    
    def _create_workloads(self, name, spec):
        # Create custom workloads
        pass
    
    def _update_workloads(self, name, spec):
        # Update custom workloads
        pass
    
    def _delete_workloads(self, name):
        # Delete custom workloads
        pass
    
    def _synthesize_status(self, name, spec):
        # Synthesize custom status
        return {"state": "active"}
```

### Register Custom Manager

```python
from k8s_modules.registry import register_resource_group

register_resource_group("custom", CustomManager, kind="Custom")
mgr = get_manager("custom", kubeconfig_path)
```

## Error Handling

```python
from kubernetes.client.exceptions import ApiException

try:
    resource = mgr.create(spec)
except ValueError as e:
    # Validation error or resource already exists
    print(f"Invalid spec: {e}")
except ApiException as e:
    # Kubernetes API error
    print(f"K8s error: {e.status} - {e.reason}")
```

## Examples

See [`examples_crud_usage.py`](../examples_crud_usage.py) for comprehensive examples covering:

- Config-only CRDs (Model)
- HarnessRuntime servers with Deployments and Services
- Batch HarnessRuntimes (Jobs)
- TrainLoops (Jobs and CronJobs)
- AutoScalers
- Secret handling (Gateway credentials)
- Registry interface
- Convenience functions

Run examples:

```bash
python examples_crud_usage.py
```

## Files

- `k8s_modules/resource_store.py` - ConfigMap/Secret persistence helpers
- `k8s_modules/base_resource.py` - Abstract base manager with CRUD
- `k8s_modules/resources/config_only.py` - Config-only resource manager
- `k8s_modules/resources/harness_runtime.py` - HarnessRuntime manager (Deployments/Services/Jobs/CronJobs)
- `k8s_modules/resources/tool_server.py` - ToolServer manager (Deployments/Services)
- `k8s_modules/resources/train_loop.py` - TrainLoop manager (Jobs/CronJobs)
- `k8s_modules/registry.py` - CRD registry (`CRD_KINDS`, `get_kind`) and convenience functions
- `../schemas/<group>-schema.json` - One schema per CRD, plus shared `common-schema.json`

## Requirements

- Python 3.8+
- kubernetes>=28.0.0
- jsonschema>=4.17.0
- PyYAML>=6.0.0
- Access to a Kubernetes cluster

## Testing

```python
# Test namespace creation and basic CRUD
from k8s_modules.registry import get_manager

mgr = get_manager("model", "/path/to/kubeconfig")

# Create
spec = {
    "kind": "Model",
    "metadata": {"name": "test-model"},
    "spec": {
        "modelName": "Test Model",
        "modelHub": "huggingface",
        "hubModelId": "org/test-model"
    }
}
created = mgr.create(spec)
assert created['metadata']['name'] == 'test-model'
assert created['apiVersion'] == 'ai.agentbox.io/v1beta1'
assert created['status']['state'] == 'active'

# Read
resource = mgr.get("test-model")
assert resource is not None

# Update
updated = mgr.update("test-model", {"spec": {"supportsStreaming": True}})
assert updated['spec']['supportsStreaming'] is True

# Delete
mgr.delete("test-model")
assert mgr.get("test-model") is None
```

## Troubleshooting

### Namespace doesn't exist

The `agentbox-system` namespace is created automatically on first use. If you encounter issues:

```bash
kubectl create namespace agentbox-system
```

### Workloads not created

Check manager logs and ensure the spec is valid:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Secrets not stored properly

Verify secret fields are detected or explicitly specified:

```python
# Auto-detection
spec = {"apiKey": "secret"}  # Will be detected

# Explicit
mgr.create(spec, secret_fields=["api_key"])
```

### Status shows 'unknown'

This means the workload doesn't exist yet or hasn't been created. Check:

```bash
kubectl get deployments,jobs,cronjobs -n agentbox-system
```

## License

See main project LICENSE file.

