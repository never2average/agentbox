# AgentBox CRUD Resource Managers

Kubernetes-backed CRUD managers for AgentBox resources. No new CRDs required - uses ConfigMaps, Secrets, and native Kubernetes workloads.

## Overview

This implementation provides CRD-like behavior for managing AgentBox resources (agents, runtimes, channels, etc.) without requiring custom resource definitions. Resources are stored in ConfigMaps and Secrets in the `agentbox-system` namespace, with appropriate Kubernetes workloads (Deployments, Services, Jobs, CronJobs) created as needed.

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

### Resource Mappings

| Resource Group | Kubernetes Workload | Storage |
|----------------|---------------------|---------|
| **runtimes** (server/worker) | Deployment + Service | ConfigMap + Secret |
| **runtimes** (batch) | Job | ConfigMap + Secret |
| **runtimes** (cron) | CronJob | ConfigMap + Secret |
| **background** (scheduled) | CronJob | ConfigMap + Secret |
| **background** (one-time) | Job | ConfigMap + Secret |
| **agents** | None | ConfigMap + Secret |
| **channels** | None | ConfigMap + Secret |
| **gateways** | None | ConfigMap + Secret |
| **models** | None | ConfigMap + Secret |
| **policies** | None | ConfigMap + Secret |
| **governance** | None | ConfigMap + Secret |
| **hardware** | None | ConfigMap + Secret |
| **io** | None | ConfigMap + Secret |
| **logs** | None | ConfigMap + Secret |
| **metric** | None | ConfigMap + Secret |
| **notifications** | None | ConfigMap + Secret |
| **recipe** | None | ConfigMap + Secret |
| **escalations** | None | ConfigMap + Secret |
| **evals** | None | ConfigMap + Secret |
| **tools** | None | ConfigMap + Secret |

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Ensure you have access to a Kubernetes cluster
export KUBECONFIG=/path/to/kubeconfig
```

## Usage

### Basic Example

```python
from k8s_modules.registry import get_manager

# Get manager for a resource group
mgr = get_manager("runtimes", kubeconfig_path="/path/to/kubeconfig")

# Create a runtime
runtime_spec = {
    "metadata": {
        "runtime_id": "my-api",
        "name": "My API Server",
        "version": "1.0.0",
        "kind": "server"
    },
    "spec": {
        "compute": {
            "cpu": {"cores": 2, "memory_mb": 2048}
        },
        "code": {
            "image": "nginx:latest"
        },
        "endpoints": [{
            "endpoint_id": "api",
            "interface": "http",
            "path": "/api"
        }]
    }
}

created = mgr.create(runtime_spec)
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
from k8s_modules.registry import ResourceRegistry, list_resource_groups

# List all available resource groups
print(list_resource_groups())
# ['agents', 'background', 'channels', 'escalations', 'evals', 'gateways', ...]

# Create registry instance
registry = ResourceRegistry(kubeconfig_path="/path/to/kubeconfig")

# Access different managers
runtimes = registry.get("runtimes")
agents = registry.get("agents")
models = registry.get("models")

# Use managers
runtimes.create(runtime_spec)
agents.list()
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
resource = create_resource("runtimes", spec, kubeconfig)

# Read
resource = get_resource("runtimes", "my-runtime", kubeconfig)

# Update
updated = update_resource("runtimes", "my-runtime", new_spec, kubeconfig)

# Delete
delete_resource("runtimes", "my-runtime", kubeconfig)

# List
all_runtimes = list_resources("runtimes", kubeconfig)
```

## Secret Handling

### Automatic Detection

Fields with these patterns are automatically stored in Secrets:

- Fields ending in: `password`, `token`, `secret`, `_key`, `api_key`
- Fields containing: `credentials`

### Explicit Specification

```python
spec = {
    "name": "my-channel",
    "config": {
        "slack": {
            "webhook_url": "https://hooks.slack.com/services/SECRET",
            "api_token": "xoxb-SECRET"
        }
    }
}

# Explicitly mark fields as secret using dot notation
mgr.create(spec, secret_fields=["config.slack.webhook_url", "config.slack.api_token"])
```

### Retrieving Secrets

```python
# Get without secrets (default) - sensitive fields omitted
resource = mgr.get("my-channel")
# config.slack.webhook_url will not be present

# Get with secrets
resource = mgr.get("my-channel", include_secrets=True)
# config.slack.webhook_url will be present
```

## Runtime Examples

### Server Runtime (Deployment + Service)

```python
mgr = get_manager("runtimes", kubeconfig_path)

runtime = {
    "metadata": {
        "runtime_id": "web-api",
        "kind": "server"
    },
    "spec": {
        "compute": {
            "cpu": {"cores": 4, "memory_mb": 4096}
        },
        "code": {
            "image": "myapp:v1.0.0",
            "entrypoint": "/app/server",
            "args": ["--port", "8080"]
        },
        "endpoints": [
            {
                "endpoint_id": "api",
                "interface": "http",
                "path": "/api/v1"
            }
        ]
    }
}

created = mgr.create(runtime)
# Creates: Deployment + Service in agentbox-system namespace
```

### Batch Runtime (Job)

```python
batch = {
    "metadata": {
        "runtime_id": "data-processor",
        "kind": "batch"
    },
    "spec": {
        "code": {
            "image": "processor:latest",
            "command": ["python", "process.py"]
        }
    }
}

created = mgr.create(batch)
# Creates: Job in agentbox-system namespace
```

### Cron Runtime (CronJob)

```python
cron = {
    "metadata": {
        "runtime_id": "daily-cleanup",
        "kind": "cron"
    },
    "spec": {
        "code": {
            "image": "cleanup:latest"
        },
        "schedule": {
            "type": "cron",
            "cron_expression": "0 2 * * *",
            "timezone": "UTC"
        }
    }
}

created = mgr.create(cron)
# Creates: CronJob in agentbox-system namespace
```

## Background Task Examples

### One-Time Task (Job)

```python
mgr = get_manager("background", kubeconfig_path)

task = {
    "name": "migration",
    "task": {
        "image": "migrator:latest",
        "command": ["python", "migrate.py"],
        "env": {
            "DATABASE_URL": "postgresql://host/db"
        }
    }
}

created = mgr.create(task)
# Creates: Job in agentbox-system namespace
```

### Scheduled Task (CronJob)

```python
scheduled = {
    "name": "backup",
    "task": {
        "image": "backup:latest",
        "command": ["./backup.sh"]
    },
    "schedule": {
        "type": "cron",
        "cron_expression": "0 3 * * *"
    }
}

created = mgr.create(scheduled)
# Creates: CronJob in agentbox-system namespace
```

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

All resources are validated against their JSON schemas in the `schemas/` directory before creation or update:

```python
# Invalid spec will raise ValueError with details
try:
    mgr.create(invalid_spec)
except ValueError as e:
    print(f"Validation error: {e}")
```

## Name Resolution

Resource names are extracted and sanitized automatically:

1. Try `metadata.runtime_id` (for runtimes)
2. Try `metadata.id`
3. Try `metadata.name`
4. Try `id`
5. Try `name`

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

register_resource_group("custom", CustomManager)
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

See `examples_crud_usage.py` for comprehensive examples covering:

- Config-only resources
- Runtime servers with Deployments and Services
- Batch Jobs
- CronJobs
- Background tasks
- Secret handling
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
- `k8s_modules/resources/runtimes.py` - Runtimes manager (Deployments/Jobs/CronJobs)
- `k8s_modules/resources/background.py` - Background tasks manager
- `k8s_modules/registry.py` - Resource registry and convenience functions

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

mgr = get_manager("agents", "/path/to/kubeconfig")

# Create
spec = {"name": "test-agent", "start_node": "s1", "graph": {}}
created = mgr.create(spec)
assert created['name'] == 'test-agent'
assert created['status']['state'] == 'active'

# Read
resource = mgr.get("test-agent")
assert resource is not None

# Update
updated = mgr.update("test-agent", {"name": "test-agent", "start_node": "s2"})
assert updated['start_node'] == 's2'

# Delete
mgr.delete("test-agent")
assert mgr.get("test-agent") is None
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
spec = {"api_key": "secret"}  # Will be detected

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

