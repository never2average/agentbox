#!/usr/bin/env python3
"""
AI-CTL: AI-powered Kubernetes cluster management CLI
"""
import os
import json
import click
from pathlib import Path
from typing import Optional
from agents import cluster_query, rca_researcher, implementation_in_cluster
from k8s_modules import connection


# Configuration directory for storing cluster configs
CONFIG_DIR = Path.home() / ".ai-ctl"
CLUSTERS_FILE = CONFIG_DIR / "clusters.json"


def ensure_config_dir():
    """Ensure the configuration directory exists."""
    CONFIG_DIR.mkdir(exist_ok=True)
    if not CLUSTERS_FILE.exists():
        CLUSTERS_FILE.write_text(json.dumps({"clusters": [], "active_cluster": None}, indent=2))


def load_clusters():
    """Load stored cluster configurations."""
    ensure_config_dir()
    with open(CLUSTERS_FILE, 'r') as f:
        return json.load(f)


def save_clusters(data):
    """Save cluster configurations."""
    ensure_config_dir()
    with open(CLUSTERS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """AI-CTL: AI-powered Kubernetes cluster management tool."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command('add-cluster')
@click.argument('kubeconfig', type=click.Path(exists=True))
@click.option('--name', help='Cluster name (optional)')
def add_cluster(kubeconfig: str, name: Optional[str]):
    """
    Add a Kubernetes cluster using a kubeconfig file.
    
    Example:
        ai-ctl add-cluster /path/to/kubeconfig --name prod-cluster
    """
    try:
        kubeconfig_path = Path(kubeconfig).resolve()
        
        # Read and validate kubeconfig
        import yaml
        with open(kubeconfig_path, 'r') as f:
            config = yaml.safe_load(f)
        
        if not config or 'clusters' not in config:
            click.echo(click.style("❌ Invalid kubeconfig file", fg='red'))
            return
        
        # Extract cluster name from kubeconfig if not provided
        if not name:
            if config.get('clusters') and len(config['clusters']) > 0:
                name = config['clusters'][0].get('name', 'default-cluster')
            else:
                name = 'default-cluster'
        
        # Store cluster configuration
        clusters_data = load_clusters()
        
        # Check if cluster already exists
        existing = next((c for c in clusters_data['clusters'] if c['name'] == name), None)
        if existing:
            click.echo(click.style(f"⚠️  Cluster '{name}' already exists. Updating...", fg='yellow'))
            clusters_data['clusters'] = [c for c in clusters_data['clusters'] if c['name'] != name]
        
        clusters_data['clusters'].append({
            'name': name,
            'kubeconfig': str(kubeconfig_path),
            'added_at': str(Path.ctime(kubeconfig_path))
        })
        
        save_clusters(clusters_data)
        
        click.echo(click.style(f"✅ Successfully added cluster: {name}", fg='green'))
        click.echo(f"   Kubeconfig: {kubeconfig_path}")
        
        # Test connection
        conn_info = connection.verify_connection(str(kubeconfig_path))
        if conn_info['connected']:
            click.echo(f"   Kubernetes Version: {conn_info['version']}")
            click.echo(click.style("   ✓ Connection verified", fg='green'))
        else:
            click.echo(click.style(f"   ⚠️  Warning: Could not verify connection: {conn_info['error']}", fg='yellow'))
    
    except FileNotFoundError:
        click.echo(click.style(f"❌ Kubeconfig file not found: {kubeconfig}", fg='red'))
    except Exception as e:
        click.echo(click.style(f"❌ Error adding cluster: {str(e)}", fg='red'))


@cli.command('query-cluster')
@click.argument('query', type=str)
def query_cluster_cmd(query: str):
    """
    Query a Kubernetes cluster using natural language.
    
    Example:
        ai-ctl query-cluster "show all pods in default namespace"
        ai-ctl query-cluster "list services"
    """
    try:
        clusters_data = load_clusters()
        
        if not clusters_data['clusters']:
            click.echo(click.style("❌ No clusters configured. Add a cluster first using 'add-cluster'", fg='red'))
            return
        
        # Get active cluster
        active_cluster_name = clusters_data.get('active_cluster')
        if active_cluster_name:
            selected_cluster = next((c for c in clusters_data['clusters'] if c['name'] == active_cluster_name), None)
            if not selected_cluster:
                selected_cluster = clusters_data['clusters'][0]
        else:
            selected_cluster = clusters_data['clusters'][0]
        
        click.echo(click.style(f"ℹ️  Using cluster: {selected_cluster['name']}", fg='cyan'))
        
        # Invoke the cluster query agent
        cluster_query.invoke_agent(query, selected_cluster['kubeconfig'])
    
    except Exception as e:
        click.echo(click.style(f"❌ Error querying cluster: {str(e)}", fg='red'))


@cli.command('rca-researcher')
@click.argument('query', type=str)
def rca_researcher_cmd(query: str):
    """
    Perform Root Cause Analysis (RCA) research on cluster issues.
    
    Example:
        ai-ctl rca-researcher "pod crashing in production"
        ai-ctl rca-researcher "high memory usage"
    """
    try:
        clusters_data = load_clusters()
        
        if not clusters_data['clusters']:
            click.echo(click.style("❌ No clusters configured. Add a cluster first using 'add-cluster'", fg='red'))
            return
        
        # Get active cluster
        active_cluster_name = clusters_data.get('active_cluster')
        if active_cluster_name:
            selected_cluster = next((c for c in clusters_data['clusters'] if c['name'] == active_cluster_name), None)
            if not selected_cluster:
                selected_cluster = clusters_data['clusters'][0]
        else:
            selected_cluster = clusters_data['clusters'][0]
        
        click.echo(f"   Cluster: {selected_cluster['name']}")
        
        # Invoke the RCA researcher agent
        rca_researcher.invoke_agent(query, selected_cluster['kubeconfig'])
    
    except Exception as e:
        click.echo(click.style(f"❌ Error during RCA research: {str(e)}", fg='red'))


@cli.command('implement-in-cluster')
@click.argument('query', type=str)
@click.option('--dry-run', is_flag=True, help='Show what would be done without applying')
def implement_in_cluster_cmd(query: str, dry_run: bool):
    """
    Implement changes or resources in a Kubernetes cluster based on natural language.
    
    Example:
        ai-ctl implement-in-cluster "create nginx deployment" --dry-run
        ai-ctl implement-in-cluster "scale deployment to 3 replicas"
    """
    try:
        clusters_data = load_clusters()
        
        if not clusters_data['clusters']:
            click.echo(click.style("❌ No clusters configured. Add a cluster first using 'add-cluster'", fg='red'))
            return
        
        # Get active cluster
        active_cluster_name = clusters_data.get('active_cluster')
        if active_cluster_name:
            selected_cluster = next((c for c in clusters_data['clusters'] if c['name'] == active_cluster_name), None)
            if not selected_cluster:
                selected_cluster = clusters_data['clusters'][0]
        else:
            selected_cluster = clusters_data['clusters'][0]
        
        click.echo(f"   Cluster: {selected_cluster['name']}")
        
        # Invoke the implementation in cluster agent
        implementation_in_cluster.invoke_agent(query, selected_cluster['kubeconfig'], dry_run)
    
    except Exception as e:
        click.echo(click.style(f"❌ Error implementing in cluster: {str(e)}", fg='red'))


@cli.command('switch-cluster')
@click.argument('cluster_name', type=str)
def switch_cluster(cluster_name: str):
    """
    Switch to a different Kubernetes cluster.
    
    Example:
        ai-ctl switch-cluster prod-cluster
    """
    try:
        clusters_data = load_clusters()
        
        if not clusters_data['clusters']:
            click.echo(click.style("❌ No clusters configured. Add a cluster first using 'add-cluster'", fg='red'))
            return
        
        # Find the cluster
        selected_cluster = next((c for c in clusters_data['clusters'] if c['name'] == cluster_name), None)
        
        if not selected_cluster:
            click.echo(click.style(f"❌ Cluster '{cluster_name}' not found", fg='red'))
            click.echo("\nAvailable clusters:")
            for cluster in clusters_data['clusters']:
                click.echo(f"  • {cluster['name']}")
            return
        
        # Set as active cluster
        clusters_data['active_cluster'] = cluster_name
        save_clusters(clusters_data)
        
        click.echo(click.style(f"✅ Switched to cluster: {cluster_name}", fg='green'))
        
        # Test connection
        conn_info = connection.verify_connection(selected_cluster['kubeconfig'])
        if conn_info['connected']:
            click.echo(f"   Kubernetes Version: {conn_info['version']}")
            click.echo(click.style("   ✓ Connection verified", fg='green'))
        else:
            click.echo(click.style(f"   ⚠️  Warning: Could not verify connection: {conn_info['error']}", fg='yellow'))
    
    except Exception as e:
        click.echo(click.style(f"❌ Error switching cluster: {str(e)}", fg='red'))


@cli.command('list-clusters')
def list_clusters():
    """List all configured clusters."""
    try:
        clusters_data = load_clusters()
        
        if not clusters_data['clusters']:
            click.echo(click.style("No clusters configured.", fg='yellow'))
            click.echo("Add a cluster using: ai-ctl add-cluster <kubeconfig>")
            return
        
        active_cluster = clusters_data.get('active_cluster')
        
        click.echo(click.style(f"📋 Configured Clusters ({len(clusters_data['clusters'])}):", fg='blue', bold=True))
        for i, cluster in enumerate(clusters_data['clusters'], 1):
            is_active = cluster['name'] == active_cluster
            status = click.style(" (active)", fg='green', bold=True) if is_active else ""
            click.echo(f"\n{i}. {click.style(cluster['name'], fg='green', bold=True)}{status}")
            click.echo(f"   Kubeconfig: {cluster['kubeconfig']}")
    
    except Exception as e:
        click.echo(click.style(f"❌ Error listing clusters: {str(e)}", fg='red'))


if __name__ == '__main__':
    cli()

