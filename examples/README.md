# Demo: a real request, end to end

Four processes, running for real, wired together only by AgentBox objects.

```bash
kubectl apply -f https://github.com/never2average/agentbox/releases/latest/download/install.yaml
kubectl create namespace agentbox-demo
kubectl apply -n agentbox-demo -f examples/demo.yaml
kubectl -n agentbox-demo logs job/demo-agent -f
```

```
discovered tool: summarize at http://demo-tools.agentbox-demo.svc:8080/summarize
tool responded 200: {"summary": "the quick brown fox jumps", "wordCount": 9}
gateway responded 200: {"id": "demo-1", "model": "demo-small", ...}
RESULT ok: answered: summarise: the quick brown fox jumps
```

## What just happened

| Object | What ran | What it proved |
|---|---|---|
| `Model` **demo-small** | An OpenAI-compatible completion server | A Model with `spec.serving` becomes a Deployment and Service |
| `Gateway` **demo-gateway** | A router in front of it | The generated config is real LiteLLM, and the agent never learns which model it reached |
| `ToolServer` **demo-tools** | One tool over HTTP | The published catalog is how the agent found the tool — no reference in its spec |
| `HarnessRuntime` **demo-agent** | The developer's agent | The platform ran the image; the image did the thinking |
| `AIMeter` **demo-spend** | Priced the usage against a budget | See below |

The agent knows two service names. It does not know which model is behind the
gateway, where the weights live, or what the budget is. That separation is the
whole design.

## Now spend the budget

```bash
kubectl -n agentbox-demo create configmap agentbox-metrics \
  --from-literal=demo-tokens=50000

kubectl -n agentbox-demo delete job demo-agent
kubectl -n agentbox-demo logs job/demo-agent -f
```

50,000 tokens at 1 USD per 1,000 is 50 USD against a 10 USD limit:

```
tool responded 200: {"summary": "the quick brown fox jumps", "wordCount": 9}
gateway responded 429: {"error": {"type": "throttle", "source": "AIMeter/demo-spend",
                                  "message": "budget of 10 exceeded"}}
RESULT blocked: AIMeter/demo-spend
```

The agent's image did not change. The controller priced the usage, decided the
budget was spent, wrote that into the gateway's config, and the gateway refused
the request. A `Guardrail` that trips reaches the gateway by the same path.

```bash
kubectl -n agentbox-demo get aimeter demo-spend \
  -o jsonpath='{.status.currentCost} USD, exceeded={.status.budgetExceeded}'
kubectl -n agentbox-demo get gateway demo-gateway -o jsonpath='{.status.enforcing}'
```

## About the images

The four programs are inlined in `demo.yaml` as a ConfigMap and mounted with
`spec.files`, so the demo runs anywhere without a registry. A real deployment
uses your own images — nothing else about these specs would change, which is
the point.

## Cleaning up

```bash
kubectl delete namespace agentbox-demo
```
