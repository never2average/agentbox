#!/usr/bin/env python3
"""
Example usage of AgentBox CRUD Resource Managers

Every AgentBox resource is a CRD with the same envelope:

    {
      "apiVersion": "ai.agentbox.io/v1beta1",
      "kind": "<Kind>",
      "metadata": {"name": "..."},
      "spec": {...},
      "status": {...}          # synthesized by the manager, never sent
    }

apiVersion and kind are filled in automatically when omitted; metadata.name is
the resource name and is required.
"""
import json
from pathlib import Path
from k8s_modules.registry import (
    get_manager,
    get_kind,
    ResourceRegistry,
    create_resource,
    get_resource,
    update_resource,
    delete_resource,
    list_resources,
    list_resource_groups,
    CRD_KINDS
)


def example_config_only_resource():
    """Example: Create and manage a config-only CRD (Model)."""
    print("\n=== Config-Only CRD Example (Model) ===")

    # Path to your kubeconfig
    kubeconfig_path = str(Path.home() / ".kube" / "config")

    # Get manager for the Model CRD
    model_mgr = get_manager("model", kubeconfig_path)

    model = {
        "apiVersion": "ai.agentbox.io/v1beta1",
        "kind": "Model",
        "metadata": {"name": "llama-3-70b-instruct"},
        "spec": {
            "modelName": "Llama 3 70B Instruct",
            "modelHub": "huggingface",
            "hubModelId": "meta-llama/Meta-Llama-3-70B-Instruct",
            "downloadApi": {
                "hubUrl": "https://huggingface.co/meta-llama/Meta-Llama-3-70B-Instruct",
                "apiEndpoint": "https://huggingface.co/api/models/meta-llama/Meta-Llama-3-70B-Instruct",
                "downloadMethod": "huggingfaceHub",
                "requiresAuth": True,
                "authTokenEnv": "HF_TOKEN"
            }
        }
    }

    print("Creating model...")
    created = model_mgr.create(model)
    print(f"Created: {json.dumps(created, indent=2)}")

    # Get the model
    print("\nGetting model...")
    fetched = model_mgr.get("llama-3-70b-instruct")
    print(f"Retrieved: {fetched['metadata']['name']} - State: {fetched['status']['state']}")

    # Update the model (merge strategy patches spec in place)
    print("\nUpdating model...")
    updated = model_mgr.update(
        "llama-3-70b-instruct",
        {"spec": {"contextWindow": {"maxTokens": 8192}}},
        strategy="merge"
    )
    print(f"Updated context window: {updated['spec']['contextWindow']}")

    # List all models
    print("\nListing all models...")
    all_models = model_mgr.list()
    print(f"Found {len(all_models)} model(s)")

    # Delete the model
    print("\nDeleting model...")
    model_mgr.delete("llama-3-70b-instruct")
    print("Deleted successfully")


def example_harness_runtime_server():
    """Example: Create a HarnessRuntime backed by a Deployment and Service."""
    print("\n=== HarnessRuntime Server Example (Deployment + Service) ===")

    kubeconfig_path = str(Path.home() / ".kube" / "config")
    harness_mgr = get_manager("harness-runtime", kubeconfig_path)

    harness = {
        "apiVersion": "ai.agentbox.io/v1beta1",
        "kind": "HarnessRuntime",
        "metadata": {
            "name": "api-harness",
            "labels": {"app.kubernetes.io/version": "1.4.0"},
        },
        "spec": {
            "runtimeKind": "server",
            "compute": {
                "cpu": {"cores": 2, "memoryMb": 2048}
            },
            "code": {
                "image": "acme/support-agent:1.4.0",
                "entrypoint": "/app/serve"
            },
            "replicas": 2,
            "endpoints": [
                {
                    "name": "api",
                    "interface": "http",
                    "port": 8080,
                    "path": "/api"
                }
            ],
            "health": {"type": "http", "path": "/healthz", "port": 8080},
            "env": {"AGENT_PROFILE": "support"}
        }
    }

    print("Creating harness runtime (will create Deployment + Service)...")
    created = harness_mgr.create(harness)
    print(f"Created: {created['metadata']['name']}")
    print(f"Status: {json.dumps(created['status'], indent=2)}")

    # Get with live status
    print("\nGetting harness runtime with live status...")
    fetched = harness_mgr.get("api-harness")
    if fetched:
        print(f"Harness: {fetched['metadata']['name']}")
        print(f"State: {fetched['status']['state']}")
        print(f"Ready replicas: {fetched['status'].get('ready_replicas', 0)}")

    # Update image
    print("\nUpdating harness image...")
    updated = harness_mgr.update(
        "api-harness",
        {"spec": {"code": {"image": "acme/support-agent:1.5.0"}}},
        strategy="merge"
    )
    print(f"Updated image to: {updated['spec']['code']['image']}")

    # Clean up
    print("\nCleaning up harness runtime...")
    harness_mgr.delete("api-harness")
    print("Deleted successfully")


def example_harness_runtime_batch():
    """Example: Create a batch HarnessRuntime (Job)."""
    print("\n=== HarnessRuntime Batch Example (Job) ===")

    kubeconfig_path = str(Path.home() / ".kube" / "config")
    harness_mgr = get_manager("harness-runtime", kubeconfig_path)

    batch = {
        "kind": "HarnessRuntime",
        "metadata": {"name": "data-processor"},
        "spec": {
            "runtimeKind": "batch",
            "code": {
                "image": "python:3.11-slim",
                "entrypoint": "python",
                "args": ["-c", "print('Processing data...')"]
            }
        }
    }

    print("Creating batch harness (apiVersion is filled in automatically)...")
    created = harness_mgr.create(batch)
    print(f"Created: {created['metadata']['name']} ({created['apiVersion']})")
    print(f"Job status: {created['status']['state']}")

    # Poll for completion
    import time
    print("\nWaiting for job to complete...")
    for i in range(10):
        job = harness_mgr.get("data-processor")
        if job:
            state = job['status']['state']
            print(f"  Attempt {i+1}: State = {state}")
            if state in ['completed', 'failed']:
                break
        time.sleep(2)

    # Clean up
    print("\nCleaning up job...")
    harness_mgr.delete("data-processor")
    print("Deleted successfully")


def example_train_loop():
    """Example: Create a TrainLoop (Job or CronJob)."""
    print("\n=== TrainLoop Example ===")

    kubeconfig_path = str(Path.home() / ".kube" / "config")
    train_mgr = get_manager("train-loop", kubeconfig_path)

    # One-time training run (Job)
    one_off = {
        "kind": "TrainLoop",
        "metadata": {"name": "sft-once"},
        "spec": {
            "type": "training",
            "version": "1.0.0",
            "status": "active",
            "worker": {
                "image": "acme/trainer:1.0.0",
                "env": {"EPOCHS": "3", "BASE_MODEL": "llama-3-70b-instruct"}
            },
            "execution": {"mode": "continuous", "timeoutSeconds": 7200}
        }
    }

    print("Creating one-time training loop (Job)...")
    created = train_mgr.create(one_off)
    print(f"Created: {created['metadata']['name']}")
    print(f"State: {created['status']['state']}")

    # Scheduled training run (CronJob)
    nightly = {
        "kind": "TrainLoop",
        "metadata": {"name": "sft-nightly"},
        "spec": {
            "type": "training",
            "version": "1.0.0",
            "status": "active",
            "worker": {
                "image": "acme/trainer:1.0.0",
                "env": {"HF_TOKEN": "hf_secret_value"}
            },
            "execution": {
                "mode": "scheduled",
                "timeoutSeconds": 7200,
                "schedule": {"type": "cron", "cronExpression": "0 2 * * *"}
            },
            "lifecycle": {"restartPolicy": {"enabled": True, "maxRestarts": 2}}
        }
    }

    print("\nCreating scheduled training loop (CronJob)...")
    scheduled = train_mgr.create(nightly, secret_fields=["spec.worker.env.HF_TOKEN"])
    print(f"Created: {scheduled['metadata']['name']}")
    print(f"Schedule: {scheduled['status'].get('schedule', 'N/A')}")

    # Clean up
    print("\nCleaning up training loops...")
    train_mgr.delete("sft-once")
    train_mgr.delete("sft-nightly")
    print("Deleted successfully")


def example_autoscaler():
    """Example: Attach a ModelAutoScaler to a Model."""
    print("\n=== ModelAutoScaler Example ===")

    kubeconfig_path = str(Path.home() / ".kube" / "config")
    autoscaler_mgr = get_manager("model-autoscaler", kubeconfig_path)

    autoscaler = {
        "kind": "ModelAutoScaler",
        "metadata": {"name": "llama-3-70b-autoscaler"},
        "spec": {
            "scaleTargetRef": {"kind": "Model", "name": "llama-3-70b-instruct"},
            "bounds": {"minReplicas": 1, "maxReplicas": 8},
            "metrics": [
                {
                    "type": "aiMetric",
                    "metric": "inference-queue-depth",
                    "target": {"metricType": "averageValue", "value": 20}
                },
                {
                    "type": "resource",
                    "resource": "gpu",
                    "target": {"metricType": "utilization", "value": 75}
                }
            ],
            "behavior": {"scaleDown": {"stabilizationWindowSeconds": 600}}
        }
    }

    print("Creating model autoscaler...")
    created = autoscaler_mgr.create(autoscaler)
    print(f"Created: {created['metadata']['name']}")
    print(f"Target: {created['spec']['scaleTargetRef']}")

    # Widen the bounds
    print("\nRaising max replicas...")
    updated = autoscaler_mgr.update(
        "llama-3-70b-autoscaler",
        {"spec": {"bounds": {"minReplicas": 1, "maxReplicas": 16}}},
        strategy="merge"
    )
    print(f"Bounds: {updated['spec']['bounds']}")

    # Clean up
    print("\nCleaning up autoscaler...")
    autoscaler_mgr.delete("llama-3-70b-autoscaler")
    print("Deleted successfully")


def example_with_secrets():
    """Example: Create a CRD with secret fields (Gateway)."""
    print("\n=== Secret Fields Example (Gateway) ===")

    kubeconfig_path = str(Path.home() / ".kube" / "config")
    gateway_mgr = get_manager("gateway", kubeconfig_path)

    gateway = {
        "kind": "Gateway",
        "metadata": {"name": "openai-compatible"},
        "spec": {
            "modelName": "llama-3-70b-instruct",
            "litellmParams": {
                "model": "openai/llama-3-70b-instruct",
                "apiBase": "http://localhost:8000/v1",
                "apiKey": "sk-XXXXXXXXXXXX",
                "rpm": 10000,
                "tpm": 1000000
            },
            "modelInfo": {"id": "llama-3-70b-instruct", "mode": "chat"}
        }
    }

    print("Creating gateway with secret fields...")
    # api_key is detected as a secret by convention and stored in a Secret
    created = gateway_mgr.create(gateway)
    print(f"Created: {created['metadata']['name']}")

    # Get without secrets (default)
    print("\nGetting gateway without secrets...")
    fetched = gateway_mgr.get("openai-compatible")
    print(f"API key in response: {fetched['spec']['litellmParams'].get('apiKey', 'REDACTED')}")

    # Get with secrets
    print("\nGetting gateway with secrets...")
    with_secrets = gateway_mgr.get("openai-compatible", include_secrets=True)
    print(f"API key: {with_secrets['spec']['litellmParams']['apiKey'][:6]}...")

    # Clean up
    print("\nCleaning up gateway...")
    gateway_mgr.delete("openai-compatible")
    print("Deleted successfully")


def example_registry_interface():
    """Example: Using the ResourceRegistry interface."""
    print("\n=== ResourceRegistry Interface Example ===")

    kubeconfig_path = str(Path.home() / ".kube" / "config")

    # Create a registry instance
    registry = ResourceRegistry(kubeconfig_path)

    # List all available CRDs
    print("Available CRDs:")
    for group in registry.list_groups():
        print(f"  - {group:26s} -> {get_kind(group)}")

    # Use the registry to access different managers
    print("\nUsing registry to create resources...")

    tool_mgr = registry.get("tool-server")
    tool_server = {
        "kind": "ToolServer",
        "metadata": {"name": "text-tools"},
        "spec": {
            "code": {"image": "acme/text-tools:2.1.0"},
            "endpoint": {"interface": "http", "port": 8080, "basePath": "/tools"},
            "tools": [
                {
                    "name": "summarize-text",
                    "description": "Summarize input text into a short abstract",
                    "path": "/summarize",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"]
                    },
                    "returns": {"type": "object"}
                }
            ],
            "replicas": 2
        }
    }
    created_tool = tool_mgr.create(tool_server)
    print(f"Created tool server: {created_tool['metadata']['name']}")

    guardrail_mgr = registry.get("guardrail")
    guardrail = {
        "kind": "Guardrail",
        "metadata": {"name": "throttle-high-rps"},
        "spec": {
            "name": "Throttle on high request rate",
            "status": "enforce",
            "priority": 10,
            "conditions": {
                "all": [
                    {
                        "metric": "request-rate",
                        "operator": "gt",
                        "threshold": 1000,
                        "statistic": "Average",
                        "periodSeconds": 60
                    }
                ]
            },
            "effect": {"type": "throttle", "parameters": {"throttlePercent": 20}}
        }
    }
    created_guardrail = guardrail_mgr.create(guardrail)
    print(f"Created guardrail: {created_guardrail['metadata']['name']}")

    # List all tool servers
    all_tools = tool_mgr.list()
    print(f"\nTotal tool servers: {len(all_tools)}")

    # Clean up
    print("\nCleaning up...")
    tool_mgr.delete("text-tools")
    guardrail_mgr.delete("throttle-high-rps")
    print("Deleted successfully")


def example_convenience_functions():
    """Example: Using the convenience functions."""
    print("\n=== Convenience Functions Example ===")

    kubeconfig_path = str(Path.home() / ".kube" / "config")

    meter = {
        "kind": "AIMeter",
        "metadata": {"name": "tenant-token-spend"},
        "spec": {
            "usage": {
                "unit": "totalTokens",
                "source": {"metric": "gateway-tokens", "statistic": "Sum"}
            },
            "attribution": {"dimensions": ["tenant_id"]},
            "window": {"type": "billingPeriod", "period": "monthly"},
            "budget": {"limit": 5000, "limitType": "cost", "onExceed": "throttle"}
        }
    }

    print("Creating meter using convenience function...")
    created = create_resource("ai-meter", meter, kubeconfig_path)
    print(f"Created: {created['metadata']['name']}")

    # Get using convenience function
    print("\nGetting meter...")
    fetched = get_resource("ai-meter", "tenant-token-spend", kubeconfig_path)
    print(f"Retrieved: {fetched['metadata']['name']} - Unit: {fetched['spec']['usage']['unit']}")

    # Update using convenience function
    print("\nUpdating meter budget...")
    updated = update_resource(
        "ai-meter",
        "tenant-token-spend",
        {"spec": {"budget": {"limit": 8000}}},
        kubeconfig_path
    )
    print(f"Updated budget: {updated['spec']['budget']['limit']}")

    # List using convenience function
    print("\nListing all meters...")
    meters = list_resources("ai-meter", kubeconfig_path)
    print(f"Found {len(meters)} meter(s)")

    # Delete using convenience function
    print("\nDeleting meter...")
    delete_resource("ai-meter", "tenant-token-spend", kubeconfig_path)
    print("Deleted successfully")


def main():
    """Run all examples."""
    print("=" * 60)
    print("AgentBox CRUD Resource Managers - Usage Examples")
    print("=" * 60)

    print(f"\nAgentBox CRDs ({len(CRD_KINDS)}):")
    for group in list_resource_groups():
        print(f"  - {group:26s} -> {get_kind(group)}")

    # Note: Uncomment the examples you want to run
    # Make sure you have a valid kubeconfig and cluster access

    print("\n" + "=" * 60)
    print("NOTE: Examples are commented out by default.")
    print("Uncomment the ones you want to run.")
    print("=" * 60)

    # example_config_only_resource()
    # example_harness_runtime_server()
    # example_harness_runtime_batch()
    # example_train_loop()
    # example_autoscaler()
    # example_with_secrets()
    # example_registry_interface()
    # example_convenience_functions()


if __name__ == "__main__":
    main()
