# Integration Example: CRUD Managers with ai-ctl CLI

This document shows how the CRUD managers can be integrated into the existing `ai-ctl` CLI.

## Option 1: Add Resource Management Commands

Add new commands to `ai_ctl.py` for direct resource management:

```python
# Add to ai_ctl.py imports
from k8s_modules.registry import get_manager, list_resource_groups
import yaml

# Add new command group
@cli.group('resource')
def resource_group():
    """Manage AgentBox resources (runtimes, agents, models, etc.)."""
    pass


@resource_group.command('create')
@click.argument('resource_group', type=click.Choice(list_resource_groups()))
@click.argument('spec_file', type=click.Path(exists=True))
@click.option('--secret-fields', '-s', multiple=True, help='Dot-notation paths to secret fields')
def create_resource_cmd(resource_group: str, spec_file: str, secret_fields: tuple):
    """
    Create a resource from a YAML spec file.
    
    Examples:
        ai-ctl resource create runtimes runtime-spec.yaml
        ai-ctl resource create channels slack-config.yaml -s config.slack.webhook_url
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
        
        name = created.get('name') or created.get('metadata', {}).get('name', 'Unknown')
        click.echo(click.style(f"✅ Created {resource_group}/{name}", fg='green'))
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
        ai-ctl resource get runtimes my-api
        ai-ctl resource get channels slack --include-secrets -o json
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
        ai-ctl resource list runtimes
        ai-ctl resource list agents -o json
    """
    try:
        clusters_data = load_clusters()
        kubeconfig_path = get_active_cluster_kubeconfig(clusters_data)
        
        mgr = get_manager(resource_group, kubeconfig_path)
        resources = mgr.list()
        
        if output == 'table':
            click.echo(f"\n{resource_group.upper()} ({len(resources)} total)")
            click.echo("=" * 80)
            for r in resources:
                name = r.get('name') or r.get('metadata', {}).get('name', 'Unknown')
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
        ai-ctl resource update runtimes my-api updated-spec.yaml
        ai-ctl resource update channels slack new-config.yaml --strategy replace
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
        ai-ctl resource delete runtimes my-api
        ai-ctl resource delete agents old-agent -y
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
    """List all available resource groups."""
    click.echo("\nAvailable resource groups:")
    for group in list_resource_groups():
        click.echo(f"  • {group}")


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
    if "create runtime" in query.lower():
        # Extract spec from query (simplified)
        spec = extract_runtime_spec_from_query(query)
        
        if dry_run:
            print(f"Would create runtime: {spec}")
            return
        
        # Use CRUD manager
        mgr = get_manager("runtimes", kubeconfig_path)
        created = mgr.create(spec)
        print(f"Created runtime: {created['metadata']['name']}")
        print(f"Status: {created['status']}")
    
    elif "scale runtime" in query.lower():
        # Extract name and replicas from query
        name, replicas = extract_scale_params(query)
        
        mgr = get_manager("runtimes", kubeconfig_path)
        runtime = mgr.get(name)
        
        # Update replica count
        runtime['spec']['replicas'] = replicas
        updated = mgr.update(name, runtime, strategy="merge")
        
        print(f"Scaled {name} to {replicas} replicas")
```

## Usage Examples

### Create a Runtime

```bash
# Create spec file
cat > my-api.yaml <<EOF
metadata:
  runtime_id: api-server
  name: My API Server
  version: 1.0.0
  kind: server
spec:
  compute:
    cpu:
      cores: 2
      memory_mb: 2048
  code:
    image: nginx:latest
  endpoints:
    - endpoint_id: api
      interface: http
      path: /api
EOF

# Create resource
ai-ctl resource create runtimes my-api.yaml
```

### List Resources

```bash
# List all runtimes
ai-ctl resource list runtimes

# List all agents in JSON
ai-ctl resource list agents -o json
```

### Get Resource Details

```bash
# Get runtime
ai-ctl resource get runtimes api-server

# Get channel with secrets
ai-ctl resource get channels slack --include-secrets
```

### Update Resource

```bash
# Update runtime
ai-ctl resource update runtimes api-server updated-spec.yaml

# Replace agent config
ai-ctl resource update agents orchestrator new-config.yaml --strategy replace
```

### Delete Resource

```bash
# Delete with confirmation
ai-ctl resource delete runtimes old-api

# Delete without confirmation
ai-ctl resource delete agents test-agent -y
```

### List Resource Groups

```bash
ai-ctl resource groups
```

## Python Library Usage

Can also be used directly as a Python library:

```python
from k8s_modules.registry import get_manager

# Initialize
mgr = get_manager("runtimes", kubeconfig_path="/path/to/kubeconfig")

# Create
runtime = {
    "metadata": {"runtime_id": "my-api", "kind": "server"},
    "spec": {"compute": {"cpu": {"cores": 2, "memory_mb": 2048}}}
}
created = mgr.create(runtime)

# Get
runtime = mgr.get("my-api")
print(f"Status: {runtime['status']['state']}")

# Update
runtime['spec']['compute']['cpu']['cores'] = 4
updated = mgr.update("my-api", runtime)

# Delete
mgr.delete("my-api")
```

## Integration with RCA Agent

```python
# In agents/rca_researcher.py

from k8s_modules.registry import get_manager

def invoke_agent(query: str, kubeconfig_path: str):
    """Perform RCA using resource information."""
    
    # Get all runtimes
    runtimes_mgr = get_manager("runtimes", kubeconfig_path)
    runtimes = runtimes_mgr.list()
    
    # Find problematic runtimes
    failing = [r for r in runtimes if r['status']['state'] in ['failed', 'degraded']]
    
    if failing:
        print(f"Found {len(failing)} failing runtimes:")
        for runtime in failing:
            name = runtime['metadata']['name']
            status = runtime['status']
            print(f"\n{name}:")
            print(f"  State: {status['state']}")
            print(f"  Ready: {status.get('ready_replicas', 0)}/{status.get('replicas', 0)}")
            
            # Get detailed info
            full_runtime = runtimes_mgr.get(name, include_secrets=False)
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
│   ├── resources.py             # Existing
│   ├── resource_store.py        # NEW - Storage layer
│   ├── base_resource.py         # NEW - Base manager
│   ├── registry.py              # NEW - Resource registry
│   └── resources/               # NEW - Manager implementations
│       ├── __init__.py
│       ├── config_only.py
│       ├── runtimes.py
│       └── background.py
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
   ai-ctl resource get runtimes my-api -o yaml > backup.yaml
   ```

## Conclusion

The CRUD managers can be seamlessly integrated into the existing `ai-ctl` CLI, providing a powerful and user-friendly interface for managing all AgentBox resources without requiring custom CRDs.

