"""
CLI Output Utilities
Common schema and functions for formatting CLI outputs across all agents
"""
import click
from typing import List, Dict, Any, Optional


class CLIOutput:
    """Common CLI output formatter for consistent display across agents."""
    
    @staticmethod
    def header(text: str, icon: str = "📋") -> None:
        """
        Display a header with styling.
        
        Args:
            text: Header text
            icon: Icon/emoji to display (optional)
        """
        click.echo(click.style(f"{icon} {text}", fg='blue', bold=True))
    
    @staticmethod
    def subheader(text: str, icon: str = "") -> None:
        """
        Display a subheader with styling.
        
        Args:
            text: Subheader text
            icon: Icon/emoji to display (optional)
        """
        icon_str = f"{icon} " if icon else ""
        click.echo(click.style(f"\n{icon_str}{text}", fg='cyan', bold=True))
    
    @staticmethod
    def success(text: str, icon: str = "✅") -> None:
        """
        Display a success message.
        
        Args:
            text: Success message text
            icon: Icon/emoji to display
        """
        click.echo(click.style(f"{icon} {text}", fg='green'))
    
    @staticmethod
    def error(text: str, icon: str = "❌") -> None:
        """
        Display an error message.
        
        Args:
            text: Error message text
            icon: Icon/emoji to display
        """
        click.echo(click.style(f"{icon} {text}", fg='red'))
    
    @staticmethod
    def warning(text: str, icon: str = "⚠️") -> None:
        """
        Display a warning message.
        
        Args:
            text: Warning message text
            icon: Icon/emoji to display
        """
        click.echo(click.style(f"{icon} {text}", fg='yellow'))
    
    @staticmethod
    def info(text: str, icon: str = "ℹ️") -> None:
        """
        Display an info message.
        
        Args:
            text: Info message text
            icon: Icon/emoji to display
        """
        click.echo(click.style(f"{icon} {text}", fg='cyan'))
    
    @staticmethod
    def text(text: str, indent: int = 0) -> None:
        """
        Display plain text with optional indentation.
        
        Args:
            text: Text to display
            indent: Number of spaces to indent
        """
        prefix = " " * indent
        click.echo(f"{prefix}{text}")
    
    @staticmethod
    def list_item(text: str, indent: int = 2) -> None:
        """
        Display a list item.
        
        Args:
            text: List item text
            indent: Number of spaces to indent
        """
        prefix = " " * indent
        click.echo(f"{prefix}• {text}")


class ResourceDisplay:
    """Display Kubernetes resources in a consistent format."""
    
    @staticmethod
    def pods(pods: List[Dict[str, Any]], namespace: str) -> None:
        """
        Display a list of pods.
        
        Args:
            pods: List of pod dictionaries
            namespace: Namespace name
        """
        CLIOutput.subheader(f"Pods in namespace '{namespace}'", icon="📦")
        
        if not pods:
            CLIOutput.text("  No pods found", indent=2)
            return
        
        for pod in pods:
            status = pod['status']
            status_color = 'green' if status == 'Running' else 'yellow'
            click.echo(f"  • {pod['name']} - {click.style(status, fg=status_color)}")
    
    @staticmethod
    def services(services: List[Dict[str, Any]], namespace: str) -> None:
        """
        Display a list of services.
        
        Args:
            services: List of service dictionaries
            namespace: Namespace name
        """
        CLIOutput.subheader(f"Services in namespace '{namespace}'", icon="🌐")
        
        if not services:
            CLIOutput.text("  No services found", indent=2)
            return
        
        for svc in services:
            click.echo(f"  • {svc['name']} ({svc['type']})")
    
    @staticmethod
    def nodes(nodes: List[Dict[str, Any]]) -> None:
        """
        Display a list of nodes.
        
        Args:
            nodes: List of node dictionaries
        """
        CLIOutput.subheader("Cluster Nodes", icon="🖥️")
        
        if not nodes:
            CLIOutput.text("  No nodes found", indent=2)
            return
        
        for node in nodes:
            status = node['status']
            status_color = 'green' if status == 'Ready' else 'red'
            click.echo(f"  • {node['name']} - {click.style(status, fg=status_color)}")
    
    @staticmethod
    def namespaces(namespaces: List[Dict[str, Any]]) -> None:
        """
        Display a list of namespaces.
        
        Args:
            namespaces: List of namespace dictionaries
        """
        CLIOutput.subheader("Namespaces", icon="📁")
        
        if not namespaces:
            CLIOutput.text("  No namespaces found", indent=2)
            return
        
        for ns in namespaces:
            click.echo(f"  • {ns['name']}")
    
    @staticmethod
    def cluster_overview(overview: Dict[str, Any]) -> None:
        """
        Display cluster overview.
        
        Args:
            overview: Overview dictionary with cluster stats
        """
        CLIOutput.subheader("Cluster Overview", icon="📊")
        click.echo(f"  Namespaces: {overview['namespace_count']}")
        click.echo(f"  Nodes: {overview['node_count']}")
        if 'total_pods' in overview:
            click.echo(f"  Total Pods: {overview['total_pods']}")
        if 'running_pods' in overview:
            click.echo(f"  Running Pods: {overview['running_pods']}")
    
    @staticmethod
    def deployments(deployments: List[Dict[str, Any]], namespace: str) -> None:
        """
        Display a list of deployments.
        
        Args:
            deployments: List of deployment dictionaries
            namespace: Namespace name
        """
        CLIOutput.subheader(f"Deployments in namespace '{namespace}'", icon="🚀")
        
        if not deployments:
            CLIOutput.text("  No deployments found", indent=2)
            return
        
        for deploy in deployments:
            ready = deploy.get('ready_replicas', 0)
            desired = deploy.get('replicas', 0)
            click.echo(f"  • {deploy['name']} - {ready}/{desired} replicas ready")


class DiagnosticsDisplay:
    """Display diagnostics and RCA information."""
    
    @staticmethod
    def pod_issues(pod_issues: Dict[str, List[Dict[str, Any]]], max_display: int = 5) -> List[str]:
        """
        Display pod issues (failed, pending, crashing).
        
        Args:
            pod_issues: Dictionary with 'failed', 'pending', 'crashing' pod lists
            max_display: Maximum number of items to display per category
            
        Returns:
            List of issue descriptions
        """
        issues_found = []
        
        # Display crashing pods
        if pod_issues['crashing']:
            click.echo(click.style(f"\n⚠️  Found {len(pod_issues['crashing'])} crashing pods:", fg='red', bold=True))
            for pod in pod_issues['crashing'][:max_display]:
                click.echo(f"  • {pod['name']}")
                if pod.get('reason'):
                    click.echo(f"    Reason: {pod['reason']}")
                if pod.get('message'):
                    click.echo(f"    Message: {pod['message']}")
                issues_found.append(f"CrashLoopBackOff: {pod['name']}")
        
        # Display failed pods
        if pod_issues['failed']:
            click.echo(click.style(f"\n❌ Found {len(pod_issues['failed'])} failed pods:", fg='red', bold=True))
            for pod in pod_issues['failed'][:max_display]:
                click.echo(f"  • {pod['name']} - {pod['status']}")
                issues_found.append(f"Failed pod: {pod['name']}")
        
        # Display pending pods
        if pod_issues['pending']:
            click.echo(click.style(f"\n⏳ Found {len(pod_issues['pending'])} pending pods:", fg='yellow', bold=True))
            for pod in pod_issues['pending'][:max_display]:
                click.echo(f"  • {pod['name']}")
                for condition in pod.get('conditions', []):
                    click.echo(f"    Issue: {condition['reason']} - {condition['message']}")
                issues_found.append(f"Pending pod: {pod['name']}")
        
        return issues_found
    
    @staticmethod
    def warning_events(events: List[Dict[str, Any]], max_display: int = 5) -> None:
        """
        Display warning events.
        
        Args:
            events: List of event dictionaries
            max_display: Maximum number of events to display
        """
        if not events:
            return
        
        click.echo(click.style(f"\n⚠️  Recent warning events ({len(events)}):", fg='yellow', bold=True))
        for event in events[-max_display:]:
            click.echo(f"  • {event['object_name']}: {event['reason']}")
            click.echo(f"    {event['message']}")
    
    @staticmethod
    def node_health(node_health: Dict[str, Any]) -> None:
        """
        Display node health information.
        
        Args:
            node_health: Node health dictionary
        """
        if node_health['unhealthy_count'] > 0:
            click.echo(click.style(f"\n🖥️  Node Health Issues:", fg='red', bold=True))
            for node in node_health['unhealthy_nodes']:
                click.echo(f"  • {node['name']} - Not Ready")
    
    @staticmethod
    def recommendations(recommendations: List[str]) -> None:
        """
        Display actionable recommendations.
        
        Args:
            recommendations: List of recommendation strings
        """
        if not recommendations:
            return
        
        click.echo(click.style("\n💡 Recommended Actions:", fg='blue', bold=True))
        for i, recommendation in enumerate(recommendations, 1):
            click.echo(f"  {i}. {recommendation}")
    
    @staticmethod
    def rca_summary(total_issues: int, has_node_issues: bool = False) -> None:
        """
        Display RCA summary.
        
        Args:
            total_issues: Total number of issues found
            has_node_issues: Whether there are node health issues
        """
        CLIOutput.subheader("Root Cause Analysis Summary", icon="📊")
        
        if total_issues > 0 or has_node_issues:
            CLIOutput.success(f"Identified {total_issues} potential issues", icon="✓")
        else:
            CLIOutput.success("No critical issues detected in the analyzed namespace")


class ImplementationDisplay:
    """Display implementation/deployment actions."""
    
    @staticmethod
    def deployment_plan(action: str, name: str, details: Dict[str, Any], dry_run: bool = False) -> None:
        """
        Display deployment action plan.
        
        Args:
            action: Action to perform (e.g., "create", "scale")
            name: Resource name
            details: Dictionary with action details
            dry_run: Whether this is a dry run
        """
        if dry_run:
            click.echo(click.style(f"[DRY RUN] Would {action} deployment:", fg='yellow'))
        else:
            click.echo(click.style(f"{action.capitalize()} deployment...", fg='cyan'))
        
        click.echo(f"  Name: {name}")
        for key, value in details.items():
            click.echo(f"  {key.capitalize()}: {value}")
    
    @staticmethod
    def deployment_result(success: bool, action: str, name: str, error: Optional[str] = None) -> None:
        """
        Display deployment action result.
        
        Args:
            success: Whether the action succeeded
            action: Action performed
            name: Resource name
            error: Error message if failed
        """
        if success:
            CLIOutput.success(f"Successfully {action} deployment: {name}")
        else:
            CLIOutput.error(f"Failed to {action} deployment: {error or 'Unknown error'}")
    
    @staticmethod
    def scale_result(success: bool, name: str, replicas: int, error: Optional[str] = None) -> None:
        """
        Display scaling result.
        
        Args:
            success: Whether scaling succeeded
            name: Deployment name
            replicas: Number of replicas
            error: Error message if failed
        """
        if success:
            CLIOutput.success(f"Successfully scaled {name} to {replicas} replicas")
        else:
            CLIOutput.error(f"Failed to scale deployment: {error or 'Unknown error'}")
    
    @staticmethod
    def supported_actions(actions: List[str]) -> None:
        """
        Display list of supported actions.
        
        Args:
            actions: List of supported action descriptions
        """
        CLIOutput.warning("Intent not recognized. Supported actions:", icon="💡")
        for action in actions:
            click.echo(f"  • {action}")

