"""
Cluster Query Agent
Handles natural language queries to Kubernetes clusters
"""
import click
from k8s_modules import query
from utils.cli_output import CLIOutput, ResourceDisplay


def invoke_agent(user_query: str, kubeconfig: str):
    """
    Query a Kubernetes cluster using natural language.
    
    Args:
        user_query: Natural language query string
        kubeconfig: Path to the kubeconfig file
        
    Returns:
        None (prints results to console)
    """
    try:
        CLIOutput.info(f"Query: {user_query}", icon="🔍")
        CLIOutput.info("Analyzing cluster...", icon="📊")
        
        # Simple query parsing (can be enhanced with AI/NLP)
        query_lower = user_query.lower()
        
        if 'pod' in query_lower:
            namespace = 'default'
            if 'namespace' in query_lower:
                parts = user_query.split()
                for i, part in enumerate(parts):
                    if 'namespace' in part.lower() and i + 1 < len(parts):
                        namespace = parts[i + 1]
            
            pods = query.list_pods(kubeconfig, namespace=namespace)
            ResourceDisplay.pods(pods, namespace)
        
        elif 'service' in query_lower or 'svc' in query_lower:
            namespace = 'default'
            services = query.list_services(kubeconfig, namespace=namespace)
            ResourceDisplay.services(services, namespace)
        
        elif 'node' in query_lower:
            nodes = query.list_nodes(kubeconfig)
            ResourceDisplay.nodes(nodes)
        
        elif 'namespace' in query_lower:
            namespaces = query.list_namespaces(kubeconfig)
            ResourceDisplay.namespaces(namespaces)
        
        else:
            CLIOutput.warning("Query not recognized. Showing cluster overview...", icon="💡")
            overview = query.get_cluster_overview(kubeconfig)
            ResourceDisplay.cluster_overview(overview)
    
    except Exception as e:
        CLIOutput.error(f"Error querying cluster: {str(e)}")
        raise

