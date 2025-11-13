#!/usr/bin/env python3
"""
Example usage of AgentBox CRUD Resource Managers

This script demonstrates how to use the resource managers to create, read,
update, and delete various AgentBox resources backed by Kubernetes.
"""
import json
from pathlib import Path
from k8s_modules.registry import (
    get_manager,
    ResourceRegistry,
    create_resource,
    get_resource,
    update_resource,
    delete_resource,
    list_resources,
    list_resource_groups
)


def example_config_only_resource():
    """Example: Create and manage a config-only resource (agents)."""
    print("\n=== Config-Only Resource Example (Agents) ===")
    
    # Path to your kubeconfig
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    
    # Get manager for agents
    agents_mgr = get_manager("agents", kubeconfig_path)
    
    # Create an agent
    agent_spec = {
        "name": "orchestrator",
        "usage_limits": {
            "metric_id": {"$min": 100, "$max": 500}
        },
        "start_node": "state_id_1",
        "governance_controls": "gov_id_1",
        "graph": {
            "state_id_1": {
                "model_id": "model_id_1",
                "gateway_id": "gateway_id_1",
                "tool_ids": ["tool_id_1", "tool_id_2"],
                "prompts": {
                    "template": {
                        "system": "You are helpful.",
                        "user": "Do X"
                    }
                }
            }
        }
    }
    
    print("Creating agent...")
    created = agents_mgr.create(agent_spec)
    print(f"Created: {json.dumps(created, indent=2)}")
    
    # Get the agent
    print("\nGetting agent...")
    agent = agents_mgr.get("orchestrator")
    print(f"Retrieved: {agent['name']} - State: {agent['status']['state']}")
    
    # Update the agent
    print("\nUpdating agent...")
    update_spec = {
        "name": "orchestrator",
        "usage_limits": {
            "metric_id": {"$min": 200, "$max": 600}
        }
    }
    updated = agents_mgr.update("orchestrator", update_spec, strategy="merge")
    print(f"Updated limits: {updated['usage_limits']}")
    
    # List all agents
    print("\nListing all agents...")
    all_agents = agents_mgr.list()
    print(f"Found {len(all_agents)} agent(s)")
    
    # Delete the agent
    print("\nDeleting agent...")
    agents_mgr.delete("orchestrator")
    print("Deleted successfully")


def example_runtime_server():
    """Example: Create a runtime with Deployment and Service."""
    print("\n=== Runtime Server Example (Deployment + Service) ===")
    
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    runtimes_mgr = get_manager("runtimes", kubeconfig_path)
    
    runtime_spec = {
        "metadata": {
            "runtime_id": "rt_api_1",
            "name": "api-runtime",
            "version": "1.0.0",
            "kind": "server"
        },
        "spec": {
            "compute": {
                "cpu": {
                    "cores": 2,
                    "memory_mb": 2048
                }
            },
            "code": {
                "image": "nginx:latest",
                "entrypoint": "/usr/sbin/nginx",
                "args": ["-g", "daemon off;"]
            },
            "endpoints": [
                {
                    "endpoint_id": "api_endpoint",
                    "interface": "http",
                    "path": "/api"
                }
            ]
        }
    }
    
    print("Creating runtime (will create Deployment + Service)...")
    created = runtimes_mgr.create(runtime_spec)
    print(f"Created: {created['metadata']['name']}")
    print(f"Status: {json.dumps(created['status'], indent=2)}")
    
    # Get runtime with status
    print("\nGetting runtime with live status...")
    runtime = runtimes_mgr.get("rt-api-1")
    if runtime:
        print(f"Runtime: {runtime['metadata']['name']}")
        print(f"State: {runtime['status']['state']}")
        print(f"Ready replicas: {runtime['status'].get('ready_replicas', 0)}")
    
    # Update image
    print("\nUpdating runtime image...")
    update_spec = {
        "metadata": runtime_spec["metadata"],
        "spec": {
            "compute": runtime_spec["spec"]["compute"],
            "code": {
                "image": "nginx:1.25-alpine"
            }
        }
    }
    updated = runtimes_mgr.update("rt-api-1", update_spec, strategy="merge")
    print(f"Updated image to: {updated['spec']['code']['image']}")
    
    # Clean up
    print("\nCleaning up runtime...")
    runtimes_mgr.delete("rt-api-1")
    print("Deleted successfully")


def example_runtime_batch_job():
    """Example: Create a batch runtime (Job)."""
    print("\n=== Runtime Batch Example (Job) ===")
    
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    runtimes_mgr = get_manager("runtimes", kubeconfig_path)
    
    batch_spec = {
        "metadata": {
            "runtime_id": "batch_processor",
            "name": "data-processor",
            "version": "1.0.0",
            "kind": "batch"
        },
        "spec": {
            "code": {
                "image": "python:3.11-slim",
                "command": ["python", "-c"],
                "args": ["print('Processing data...'); import time; time.sleep(5); print('Done!')"]
            }
        }
    }
    
    print("Creating batch job...")
    created = runtimes_mgr.create(batch_spec)
    print(f"Created: {created['metadata']['name']}")
    print(f"Job status: {created['status']['state']}")
    
    # Poll for completion
    import time
    print("\nWaiting for job to complete...")
    for i in range(10):
        job = runtimes_mgr.get("batch-processor")
        if job:
            state = job['status']['state']
            print(f"  Attempt {i+1}: State = {state}")
            if state in ['completed', 'failed']:
                break
        time.sleep(2)
    
    # Clean up
    print("\nCleaning up job...")
    runtimes_mgr.delete("batch-processor")
    print("Deleted successfully")


def example_runtime_cronjob():
    """Example: Create a cron runtime (CronJob)."""
    print("\n=== Runtime Cron Example (CronJob) ===")
    
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    runtimes_mgr = get_manager("runtimes", kubeconfig_path)
    
    cron_spec = {
        "metadata": {
            "runtime_id": "hourly_cleanup",
            "name": "cleanup-task",
            "version": "1.0.0",
            "kind": "cron"
        },
        "spec": {
            "code": {
                "image": "busybox:latest",
                "command": ["sh", "-c"],
                "args": ["echo 'Running cleanup...'; date"]
            },
            "schedule": {
                "type": "cron",
                "cron_expression": "0 * * * *",
                "timezone": "UTC"
            }
        }
    }
    
    print("Creating cron job...")
    created = runtimes_mgr.create(cron_spec)
    print(f"Created: {created['metadata']['name']}")
    print(f"Schedule: {created['status'].get('schedule', 'N/A')}")
    print(f"State: {created['status']['state']}")
    
    # Get status
    print("\nGetting cron job status...")
    cronjob = runtimes_mgr.get("hourly-cleanup")
    if cronjob:
        print(f"Active jobs: {cronjob['status'].get('active_jobs', 0)}")
        print(f"Last schedule: {cronjob['status'].get('last_schedule_time', 'Never')}")
    
    # Clean up
    print("\nCleaning up cron job...")
    runtimes_mgr.delete("hourly-cleanup")
    print("Deleted successfully")


def example_background_task():
    """Example: Create a background task."""
    print("\n=== Background Task Example ===")
    
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    bg_mgr = get_manager("background", kubeconfig_path)
    
    # One-time task (Job)
    task_spec = {
        "name": "data-migration",
        "task": {
            "image": "python:3.11-slim",
            "command": ["python", "-c"],
            "args": ["print('Migrating data...'); import time; time.sleep(3); print('Migration complete!')"],
            "env": {
                "DATABASE_URL": "postgresql://localhost/mydb",
                "MIGRATION_VERSION": "v2.0"
            }
        },
        "retry": {
            "max_attempts": 3
        }
    }
    
    print("Creating one-time background task (Job)...")
    created = bg_mgr.create(task_spec)
    print(f"Created: {created['name']}")
    print(f"State: {created['status']['state']}")
    
    # Scheduled task (CronJob)
    scheduled_spec = {
        "name": "daily-backup",
        "task": {
            "image": "postgres:15-alpine",
            "command": ["pg_dump"],
            "args": ["-h", "localhost", "-U", "user", "mydb"],
            "env": {
                "PGPASSWORD": "secret123"
            }
        },
        "schedule": {
            "type": "cron",
            "cron_expression": "0 2 * * *"
        }
    }
    
    print("\nCreating scheduled background task (CronJob)...")
    scheduled = bg_mgr.create(scheduled_spec, secret_fields=["task.env.PGPASSWORD"])
    print(f"Created: {scheduled['name']}")
    print(f"Schedule: {scheduled['status'].get('schedule', 'N/A')}")
    
    # Clean up
    print("\nCleaning up background tasks...")
    bg_mgr.delete("data-migration")
    bg_mgr.delete("daily-backup")
    print("Deleted successfully")


def example_with_secrets():
    """Example: Create resource with secret fields."""
    print("\n=== Secret Fields Example (Channels) ===")
    
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    channels_mgr = get_manager("channels", kubeconfig_path)
    
    channel_spec = {
        "id": "slack_critical",
        "name": "Slack Critical Alerts",
        "type": "slack",
        "enabled": True,
        "priority": "critical",
        "config": {
            "slack": {
                "webhook_url": "https://hooks.slack.com/services/SECRET/TOKEN",
                "channel": "#critical-alerts",
                "username": "AgentBox Alert"
            }
        }
    }
    
    print("Creating channel with secret fields...")
    # webhook_url will be automatically detected as secret
    created = channels_mgr.create(channel_spec)
    print(f"Created: {created['name']}")
    
    # Get without secrets (default)
    print("\nGetting channel without secrets...")
    channel = channels_mgr.get("slack-critical")
    print(f"Webhook URL in response: {channel['config']['slack'].get('webhook_url', 'REDACTED')}")
    
    # Get with secrets
    print("\nGetting channel with secrets...")
    channel_with_secrets = channels_mgr.get("slack-critical", include_secrets=True)
    print(f"Webhook URL: {channel_with_secrets['config']['slack']['webhook_url'][:30]}...")
    
    # Clean up
    print("\nCleaning up channel...")
    channels_mgr.delete("slack-critical")
    print("Deleted successfully")


def example_registry_interface():
    """Example: Using ResourceRegistry interface."""
    print("\n=== ResourceRegistry Interface Example ===")
    
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    
    # Create a registry instance
    registry = ResourceRegistry(kubeconfig_path)
    
    # List all available resource groups
    print("Available resource groups:")
    for group in registry.list_groups():
        print(f"  - {group}")
    
    # Use registry to access different managers
    print("\nUsing registry to create resources...")
    
    # Create a model config
    models_mgr = registry.get("models")
    model_spec = {
        "id": "gpt4",
        "name": "GPT-4",
        "provider": "openai",
        "config": {
            "api_key": "sk-XXXXXX",
            "model": "gpt-4",
            "temperature": 0.7
        }
    }
    created_model = models_mgr.create(model_spec, secret_fields=["config.api_key"])
    print(f"Created model: {created_model['name']}")
    
    # Create a gateway config
    gateways_mgr = registry.get("gateways")
    gateway_spec = {
        "id": "main_gateway",
        "name": "Main Gateway",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1"
    }
    created_gateway = gateways_mgr.create(gateway_spec)
    print(f"Created gateway: {created_gateway['name']}")
    
    # List all models
    all_models = models_mgr.list()
    print(f"\nTotal models: {len(all_models)}")
    
    # Clean up
    print("\nCleaning up...")
    models_mgr.delete("gpt4")
    gateways_mgr.delete("main-gateway")
    print("Deleted successfully")


def example_convenience_functions():
    """Example: Using convenience functions."""
    print("\n=== Convenience Functions Example ===")
    
    kubeconfig_path = str(Path.home() / ".kube" / "config")
    
    # Create using convenience function
    policy_spec = {
        "id": "rate_limit_policy",
        "name": "Rate Limit Policy",
        "type": "rate_limit",
        "config": {
            "requests_per_minute": 100,
            "burst": 20
        }
    }
    
    print("Creating policy using convenience function...")
    created = create_resource("policies", policy_spec, kubeconfig_path)
    print(f"Created: {created['name']}")
    
    # Get using convenience function
    print("\nGetting policy...")
    policy = get_resource("policies", "rate-limit-policy", kubeconfig_path)
    print(f"Retrieved: {policy['name']} - Type: {policy['type']}")
    
    # Update using convenience function
    print("\nUpdating policy...")
    update_spec = {
        "id": "rate_limit_policy",
        "config": {
            "requests_per_minute": 200
        }
    }
    updated = update_resource("policies", "rate-limit-policy", update_spec, kubeconfig_path)
    print(f"Updated RPM: {updated['config']['requests_per_minute']}")
    
    # List using convenience function
    print("\nListing all policies...")
    policies = list_resources("policies", kubeconfig_path)
    print(f"Found {len(policies)} policy/policies")
    
    # Delete using convenience function
    print("\nDeleting policy...")
    delete_resource("policies", "rate-limit-policy", kubeconfig_path)
    print("Deleted successfully")


def main():
    """Run all examples."""
    print("=" * 60)
    print("AgentBox CRUD Resource Managers - Usage Examples")
    print("=" * 60)
    
    print("\nAvailable resource groups:")
    for group in list_resource_groups():
        print(f"  - {group}")
    
    # Note: Uncomment the examples you want to run
    # Make sure you have a valid kubeconfig and cluster access
    
    print("\n" + "=" * 60)
    print("NOTE: Examples are commented out by default.")
    print("Uncomment the ones you want to run.")
    print("=" * 60)
    
    # example_config_only_resource()
    # example_runtime_server()
    # example_runtime_batch_job()
    # example_runtime_cronjob()
    # example_background_task()
    # example_with_secrets()
    # example_registry_interface()
    # example_convenience_functions()


if __name__ == "__main__":
    main()

