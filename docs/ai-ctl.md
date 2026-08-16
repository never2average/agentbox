# ai-ctl

`ai-ctl` is a cluster-inspection CLI that ships alongside the CRDs: it queries a cluster,
researches root causes and applies changes from natural language. It is independent of the
CRD set — useful, but not required to use AgentBox.

## Installation & usage

## Installation

### Option 1: Install in development mode (recommended for testing)
```bash
# Install dependencies
pip install -r requirements.txt

# Install the CLI in development mode
pip install -e .
```

### Option 2: Direct installation
```bash
pip install .
```

### Option 3: Run directly without installation
```bash
python ai_ctl.py --help
```

## Usage

### 1. Add a Kubernetes Cluster
```bash
# Add a cluster with auto-detected name
ai-ctl add-cluster /path/to/kubeconfig

# Add a cluster with custom name
ai-ctl add-cluster /path/to/kubeconfig --name prod-cluster
```

### 2. Query a Cluster
```bash
# Query pods
ai-ctl query-cluster "show all pods in default namespace"

# Query services
ai-ctl query-cluster "list services"

# Query nodes
ai-ctl query-cluster "show cluster nodes"

# Query namespaces
ai-ctl query-cluster "list namespaces"

# Query specific cluster
ai-ctl query-cluster "show pods" --cluster prod-cluster
```

### 3. Root Cause Analysis (RCA) Research
```bash
# Analyze issues in default namespace
ai-ctl rca-researcher "pod crashing in production"

# Deep analysis with custom namespace
ai-ctl rca-researcher "high memory usage" --namespace kube-system --depth 5

# Analyze specific cluster
ai-ctl rca-researcher "connection timeout" --cluster prod-cluster
```

### 4. Implement Changes in Cluster
```bash
# Create a deployment (dry-run first)
ai-ctl implement-in-cluster "create nginx deployment" --dry-run

# Create deployment (execute)
ai-ctl implement-in-cluster "create nginx deployment"

# Scale a deployment
ai-ctl implement-in-cluster "scale deployment to 3 replicas"

# With specific namespace
ai-ctl implement-in-cluster "create redis deployment" --namespace production
```

### 5. List Configured Clusters
```bash
ai-ctl list-clusters
```

## Configuration

Cluster configurations are stored in: `~/.ai-ctl/clusters.json`

## Features

✅ **Add Cluster**: Store and manage multiple Kubernetes cluster configurations  
✅ **Query Cluster**: Natural language queries to inspect cluster resources  
✅ **RCA Researcher**: Automated root cause analysis for cluster issues  
✅ **Implement**: Execute changes in the cluster with dry-run support  
✅ **Beautiful CLI**: Colorful output with emoji indicators  

## Requirements

- Python 3.8+
- kubectl (optional, for additional operations)
- Valid kubeconfig file with cluster access

## Troubleshooting

### "No clusters configured" error
Make sure to add a cluster first using `ai-ctl add-cluster <kubeconfig>`

### Connection errors
Verify your kubeconfig is valid and you have network access to the cluster:
```bash
kubectl --kubeconfig=/path/to/kubeconfig get nodes
```

### Permission errors
Ensure your kubeconfig has sufficient RBAC permissions for the operations you're trying to perform.

