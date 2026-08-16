# CLI integration

`ai-ctl` is the cluster-inspection CLI in this repository ([guide](ai-ctl.md)). This page
shows how to wire the CRUD managers into it so `ai-ctl resource ...` can manage AgentBox
objects on clusters where CRDs are not installed.

With CRDs installed you do not need any of this — `kubectl` already works.

## Option 1: Add Resource Management Commands

Add new commands to `ai_ctl.py` for direct resource management:

```python
# Add to ai_ctl.py imports
from k8s_modules.registry import get_manager, list_resource_groups, get_kind
import yaml

# Add new command group
@cli.group('resource')
def resource_group():
    """Manage AgentBox CRDs (harness-runtime, model, gateway, etc.)."""
    pass


@resource_group.command('create')
@click.argument('resource_group', type=click.Choice(list_resource_groups()))
@click.argument('spec_file', type=click.Path(exists=True))
@click.option('--secret-fields', '-s', multiple=True, help='Dot-notation paths to secret fields')
def create_resource_cmd(resource_group: str, spec_file: str, secret_fields: tuple):
    """
    Create a resource from a YAML spec file.
    
    Examples:
        ai-ctl resource create harness-runtime harness.yaml
        ai-ctl resource create gateway gateway.yaml -s spec.litellm_params.api_key
    """
    try:
        clusters_data = load_clusters()
        if not clusters_data['clusters']:
            click.echo(click.style("❌ No clusters configured", fg='red'))
            return
        
        # Get active cluster
        kubeconfig_path = get_active_cluster_kubeconfig(clusters_data)
        
        # Load spec
        with open(spec_file, 'r') as f:
            spec = yaml.safe_load(f)
        
        # Get manager and create
        mgr = get_manager(resource_group, kubeconfig_path)
        created = mgr.create(spec, secret_fields=list(secret_fields) if secret_fields else None)
        
        name = created['metadata']['name']
        click.echo(click.style(f"✅ Created {created['kind']}/{name}", fg='green'))
        click.echo(f"   Status: {created['status']['state']}")
        
    except Exception as e:
        click.echo(click.style(f"❌ Error: {str(e)}", fg='red'))


@resource_group.command('get')
@click.argument('resource_group', type=click.Choice(list_resource_groups()))
@click.argument('name')
@click.option('--include-secrets', is_flag=True, help='Include secret fields in output')
@click.option('--output', '-o', type=click.Choice(['yaml', 'json']), default='yaml')
def get_resource_cmd(resource_group: str, name: str, include_secrets: bool, output: str):
    """
    Get a resource by name.
    
    Examples:
        ai-ctl resource get harness-runtime my-api
        ai-ctl resource get gateway openai-compatible --include-secrets -o json
    """
    try:
        clusters_data = load_clusters()
        kubeconfig_path = get_active_cluster_kubeconfig(clusters_data)
        
        mgr = get_manager(resource_group, kubeconfig_path)
        resource = mgr.get(name, include_secrets=include_secrets)
        
        if resource is None:
            click.echo(click.style(f"❌ Resource {name} not found", fg='red'))
            return
        
        if output == 'yaml':
            click.echo(yaml.dump(resource, default_flow_style=False))
        else:
            click.echo(json.dumps(resource, indent=2))
        
    except Exception as e:
        click.echo(click.style(f"❌ Error: {str(e)}", fg='red'))


@resource_group.command('list')
@click.argument('resource_group', type=click.Choice(list_resource_groups()))
@click.option('--output', '-o', type=click.Choice(['table', 'yaml', 'json']), default='table')
def list_resources_cmd(resource_group: str, output: str):
    """
    List all resources in a group.
    
    Examples:
        ai-ctl resource list harness-runtime
        ai-ctl resource list model -o json
    """
    try:
        clusters_data = load_clusters()
        kubeconfig_path = get_active_cluster_kubeconfig(clusters_data)
        
        mgr = get_manager(resource_group, kubeconfig_path)
        resources = mgr.list()
        
        if output == 'table':
            click.echo(f"\n{get_kind(resource_group)} ({len(resources)} total)")
            click.echo("=" * 80)
            for r in resources:
                name = r['metadata']['name']
                state = r.get('status', {}).get('state', 'unknown')
                click.echo(f"  • {name:40} [{state}]")
        elif output == 'yaml':
            click.echo(yaml.dump(resources, default_flow_style=False))
        else:
            click.echo(json.dumps(resources, indent=2))
        
    except Exception as e:
        click.echo(click.style(f"❌ Error: {str(e)}", fg='red'))


@resource_group.command('update')
@click.argument('resource_group', type=click.Choice(list_resource_groups()))
@click.argument('name')
@click.argument('spec_file', type=click.Path(exists=True))
@click.option('--strategy', type=click.Choice(['merge', 'replace']), default='merge')
@click.option('--secret-fields', '-s', multiple=True, help='Dot-notation paths to secret fields')
def update_resource_cmd(resource_group: str, name: str, spec_file: str, strategy: str, secret_fields: tuple):
    """
    Update a resource.
    
    Examples:
        ai-ctl resource update harness-runtime my-api updated-spec.yaml
        ai-ctl resource update gateway openai-compatible new-config.yaml --strategy replace
    """
    try:
        clusters_data = load_clusters()
        kubeconfig_path = get_active_cluster_kubeconfig(clusters_data)
        
        with open(spec_file, 'r') as f:
            spec = yaml.safe_load(f)
        
        mgr = get_manager(resource_group, kubeconfig_path)
        updated = mgr.update(
            name, 
            spec, 
            strategy=strategy,
            secret_fields=list(secret_fields) if secret_fields else None
        )
        
        click.echo(click.style(f"✅ Updated {resource_group}/{name}", fg='green'))
        click.echo(f"   Status: {updated['status']['state']}")
        
    except Exception as e:
        click.echo(click.style(f"❌ Error: {str(e)}", fg='red'))


@resource_group.command('delete')
@click.argument('resource_group', type=click.Choice(list_resource_groups()))
@click.argument('name')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
def delete_resource_cmd(resource_group: str, name: str, yes: bool):
    """
    Delete a resource.
    
    Examples:
        ai-ctl resource delete harness-runtime my-api
        ai-ctl resource delete model old-model -y
    """
    try:
        if not yes:
            confirm = click.confirm(f"Delete {resource_group}/{name}?")
            if not confirm:
                click.echo("Cancelled")
                return
        
        clusters_data = load_clusters()
        kubeconfig_path = get_active_cluster_kubeconfig(clusters_data)
        
        mgr = get_manager(resource_group, kubeconfig_path)
        mgr.delete(name)
        
        click.echo(click.style(f"✅ Deleted {resource_group}/{name}", fg='green'))
        
    except Exception as e:
        click.echo(click.style(f"❌ Error: {str(e)}", fg='red'))


@resource_group.command('groups')
def list_groups_cmd():
    """List all AgentBox CRDs."""
    click.echo("\nAvailable CRDs:")
    for group in list_resource_groups():
        click.echo(f"  • {group:26} → {get_kind(group)}")


def get_active_cluster_kubeconfig(clusters_data):
    """Helper to get active cluster kubeconfig."""
    active_cluster_name = clusters_data.get('active_cluster')
    if active_cluster_name:
        cluster = next((c for c in clusters_data['clusters'] if c['name'] == active_cluster_name), None)
        if cluster:
            return cluster['kubeconfig']
    
    if clusters_data['clusters']:
        return clusters_data['clusters'][0]['kubeconfig']
    
    raise ValueError("No clusters configured")
```

## Option 2: Use in Existing Agent Commands

Enhance existing agents to manage resources:

```python
# In agents/implementation_in_cluster.py

from k8s_modules.registry import get_manager

def invoke_agent(query: str, kubeconfig_path: str, dry_run: bool = False):
    """
    Implement changes in cluster based on natural language query.
    """
    # Parse query to determine intent
    if "create harness" in query.lower():
        # Extract spec from query (simplified)
        spec = extract_harness_spec_from_query(query)
        
        if dry_run:
            print(f"Would create harness: {spec}")
            return
        
        # Use CRUD manager
        mgr = get_manager("harness-runtime", kubeconfig_path)
        created = mgr.create(spec)
        print(f"Created harness: {created['metadata']['name']}")
        print(f"Status: {created['status']}")
    
    elif "scale harness" in query.lower():
        # Extract name and replica bounds from query
        name, max_replicas = extract_scale_params(query)
        
        # Scaling is declarative: raise the swarm autoscaler's ceiling
        mgr = get_manager("harness-swarm-autoscaler", kubeconfig_path)
        updated = mgr.update(
            name,
            {"spec": {"bounds": {"maxReplicas": max_replicas}}},
            strategy="merge"
        )
        
        print(f"Raised {name} ceiling to {max_replicas} replicas")
```

## Usage Examples

### Create a HarnessRuntime

```bash
# Create spec file
cat > my-api.yaml <<EOF
apiVersion: ai.agentbox.io/v1beta1
kind: HarnessRuntime
metadata:
  name: api-server
  version: 1.0.0
spec:
  runtimeKind: server
  compute:
    cpu:
      cores: 2
      memoryMb: 2048
  code:
    image: acme/support-agent:1.4.0
  endpoints:
    - name: api
      interface: http
      port: 8080
      path: /api
EOF

# Create resource
ai-ctl resource create harness-runtime my-api.yaml
```

### List Resources

```bash
# List all harness runtimes
ai-ctl resource list harness-runtime

# List all models in JSON
ai-ctl resource list model -o json
```

### Get Resource Details

```bash
# Get harness runtime
ai-ctl resource get harness-runtime api-server

# Get gateway with secrets
ai-ctl resource get gateway openai-compatible --include-secrets
```

### Update Resource

```bash
# Update harness runtime
ai-ctl resource update harness-runtime api-server updated-spec.yaml

# Replace guardrail config
ai-ctl resource update guardrail throttle-high-rps new-config.yaml --strategy replace
```

### Delete Resource

```bash
# Delete with confirmation
ai-ctl resource delete harness-runtime old-api

# Delete without confirmation
ai-ctl resource delete model test-model -y
```

### List CRDs

```bash
ai-ctl resource groups
```

## Python Library Usage

Can also be used directly as a Python library:

```python
from k8s_modules.registry import get_manager

# Initialize
mgr = get_manager("harness-runtime", kubeconfig_path="/path/to/kubeconfig")

# Create
harness = {
    "kind": "HarnessRuntime",
    "metadata": {"name": "my-api"},
    "spec": {
        "runtimeKind": "server",
        "code": {"image": "acme/support-agent:1.4.0"},
        "compute": {"cpu": {"cores": 2, "memoryMb": 2048}}
    }
}
created = mgr.create(harness)

# Get
harness = mgr.get("my-api")
print(f"Status: {harness['status']['state']}")

# Update
updated = mgr.update("my-api", {"spec": {"compute": {"cpu": {"cores": 4}}}})

# Delete
mgr.delete("my-api")
```

## Integration with RCA Agent

```python
# In agents/rca_researcher.py

from k8s_modules.registry import get_manager

def invoke_agent(query: str, kubeconfig_path: str):
    """Perform RCA using resource information."""
    
    # Get all harness runtimes
    harness_mgr = get_manager("harness-runtime", kubeconfig_path)
    harnesses = harness_mgr.list()
    
    # Find problematic harnesses
    failing = [h for h in harnesses if h['status']['state'] in ['failed', 'degraded']]
    
    if failing:
        print(f"Found {len(failing)} failing harnesses:")
        for harness in failing:
            name = harness['metadata']['name']
            status = harness['status']
            print(f"\n{name}:")
            print(f"  State: {status['state']}")
            print(f"  Ready: {status.get('ready_replicas', 0)}/{status.get('replicas', 0)}")
            
            # Get detailed info
            full_harness = harness_mgr.get(name, include_secrets=False)
            # Analyze...
```

## File Structure After Integration

```
agentbox/
├── ai_ctl.py                    # Enhanced CLI with resource commands
├── k8s_modules/
│   ├── __init__.py
│   ├── connection.py            # Existing
│   ├── diagnostics.py           # Existing
│   ├── query.py                 # Existing
│   ├── resource_store.py        # NEW - Storage layer
│   ├── base_resource.py         # NEW - Base manager
│   ├── registry.py              # NEW - CRD registry
│   └── resources/               # NEW - Manager implementations
│       ├── __init__.py
│       ├── config_only.py
│       ├── harness_runtime.py
│       ├── tool_server.py
│       └── train_loop.py
├── agents/
│   ├── cluster_query.py         # Can use CRUD managers
│   ├── rca_researcher.py        # Can use CRUD managers
│   └── implementation_in_cluster.py  # Can use CRUD managers
└── examples/
    ├── examples_crud_usage.py   # Comprehensive examples
    └── test_crud_basic.py       # Test suite
```

## Benefits of Integration

1. **Unified Interface**: Single CLI for all resource management
2. **Type Safety**: Schema validation for all operations
3. **Secret Handling**: Automatic secret detection and secure storage
4. **Status Tracking**: Real-time status from Kubernetes workloads
5. **Agent Enhancement**: Agents can directly manage resources
6. **No CRDs**: Works without custom resource definitions
7. **Kubernetes Native**: Uses standard Kubernetes resources

## Migration Path

If users already have resources managed by other tools:

1. **Import existing resources**:
   ```python
   # Read from existing ConfigMap/Deployment
   # Convert to AgentBox spec format
   # Create via CRUD manager
   ```

2. **Side-by-side operation**:
   - CRUD managers use specific labels
   - Won't interfere with existing resources
   - Can gradually migrate

3. **Export resources**:
   ```bash
   ai-ctl resource get harness-runtime my-api -o yaml > backup.yaml
   ```

## Conclusion

The CRUD managers can be seamlessly integrated into the existing `ai-ctl` CLI, providing a single interface for the 16 AgentBox CRDs without requiring custom resource definitions to be installed in the cluster.

