"""
RCA Researcher Agent
Performs Root Cause Analysis on Kubernetes cluster issues
"""
import click
from k8s_modules import diagnostics
from utils.cli_output import CLIOutput, DiagnosticsDisplay


def invoke_agent(user_query: str, kubeconfig: str):
    """
    Perform Root Cause Analysis (RCA) research on cluster issues.
    
    Args:
        user_query: Description of the issue to analyze
        kubeconfig: Path to the kubeconfig file
        
    Returns:
        None (prints results to console)
    """
    try:
        CLIOutput.header("Starting RCA Research", icon="🔬")
        CLIOutput.text(f"   Query: {user_query}\n")
        
        CLIOutput.subheader("Phase 1: Gathering cluster state...", icon="📋")
        
        # Perform comprehensive RCA
        rca_result = diagnostics.perform_rca(kubeconfig, namespace='default')
        
        CLIOutput.subheader("Phase 2: Analyzing issues...", icon="🔍")
        
        pod_issues = rca_result['pod_issues']
        warning_events = rca_result['warning_events']
        node_health = rca_result['node_health']
        
        # Display pod issues
        issues_found = DiagnosticsDisplay.pod_issues(pod_issues, max_display=5)
        
        # Display warning events
        DiagnosticsDisplay.warning_events(warning_events, max_display=5)
        
        # Display node health
        DiagnosticsDisplay.node_health(node_health)
        
        # Display summary
        DiagnosticsDisplay.rca_summary(
            rca_result['total_issues_found'],
            has_node_issues=(node_health['unhealthy_count'] > 0)
        )
        
        # Display recommendations
        DiagnosticsDisplay.recommendations(rca_result['recommendations'])
        
        CLIOutput.success("RCA Research Complete", icon="✨")
    
    except Exception as e:
        CLIOutput.error(f"Error during RCA research: {str(e)}")
        raise

