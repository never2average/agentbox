"""
Implementation in Cluster Agent
Implements changes or resources in Kubernetes clusters based on natural language
"""
import click
from k8s_modules import resources
from utils.cli_output import CLIOutput, ImplementationDisplay


def invoke_agent(user_query: str, kubeconfig: str, dry_run: bool = False):
    """
    Implement changes or resources in a Kubernetes cluster based on natural language.
    
    Args:
        user_query: Natural language description of what to implement
        kubeconfig: Path to the kubeconfig file
        dry_run: If True, show what would be done without applying
        
    Returns:
        None (prints results to console)
    """
    try:
        CLIOutput.header("Implementation Request", icon="🚀")
        CLIOutput.text(f"   Query: {user_query}")
        CLIOutput.text(f"   Mode: {'DRY RUN' if dry_run else 'EXECUTE'}\n")
        
        query_lower = user_query.lower()
        
        # Parse intent from query
        if 'create' in query_lower and 'deployment' in query_lower:
            CLIOutput.subheader("Creating deployment...", icon="📦")
            
            # Extract deployment name (simple parsing)
            words = user_query.split()
            name = 'nginx-deployment'
            image = 'nginx:latest'
            
            for i, word in enumerate(words):
                if word.lower() == 'nginx':
                    name = 'nginx-deployment'
                    image = 'nginx:latest'
                elif word.lower() == 'redis':
                    name = 'redis-deployment'
                    image = 'redis:latest'
            
            details = {
                'image': image,
                'namespace': 'default',
                'replicas': 1
            }
            
            ImplementationDisplay.deployment_plan('create', name, details, dry_run)
            
            if not dry_run:
                result = resources.create_deployment(
                    kubeconfig_path=kubeconfig,
                    name=name,
                    image=image,
                    namespace='default',
                    replicas=1
                )
                ImplementationDisplay.deployment_result(
                    result['success'],
                    'created',
                    result['name'],
                    result.get('error')
                )
        
        elif 'scale' in query_lower:
            CLIOutput.subheader("Scaling deployment...", icon="📊")
            
            # Extract deployment name and replica count
            words = user_query.split()
            replicas = 3
            deployment_name = None
            
            for i, word in enumerate(words):
                if word.isdigit():
                    replicas = int(word)
                elif 'deployment' in query_lower and i > 0:
                    deployment_name = words[i - 1] if not words[i - 1].lower() == 'scale' else None
            
            if not deployment_name:
                # List deployments to choose from
                deployments_list = resources.list_deployments(kubeconfig, namespace='default')
                if deployments_list:
                    deployment_name = deployments_list[0]['name']
                    CLIOutput.text(f"Using deployment: {deployment_name}")
            
            if deployment_name:
                details = {
                    'replicas': replicas,
                    'namespace': 'default'
                }
                
                ImplementationDisplay.deployment_plan('scale', deployment_name, details, dry_run)
                
                if not dry_run:
                    result = resources.scale_deployment(
                        kubeconfig_path=kubeconfig,
                        name=deployment_name,
                        replicas=replicas,
                        namespace='default'
                    )
                    ImplementationDisplay.scale_result(
                        result['success'],
                        result['name'],
                        result['replicas'],
                        result.get('error')
                    )
            else:
                CLIOutput.error("No deployment found to scale")
        
        else:
            supported_actions = [
                "create deployment <name>",
                "scale deployment to <N> replicas"
            ]
            ImplementationDisplay.supported_actions(supported_actions)
            CLIOutput.text("\nProvide a more specific query or use kubectl directly for complex operations.")
    
    except Exception as e:
        CLIOutput.error(f"Error implementing in cluster: {str(e)}")
        raise

