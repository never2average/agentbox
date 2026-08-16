# Test cases

Everything worth running against the AgentBox CRDs, whether or not it is automated yet.

`tests/e2e_test.py` currently automates 244 assertions against a live API server. This is
the wider catalogue: what that suite covers, and what a serious production rollout would
want on top of it.

| | |
|---|---|
| ✅ | Automated in `tests/e2e_test.py` |
| 🔶 | Partly automated — the happy path runs, the variations do not |
| ⬜ | Not automated yet |

Run the automated set with:

```bash
kind create cluster --name agentbox-e2e --kubeconfig /tmp/agentbox-kubeconfig
python tests/e2e_test.py --context kind-agentbox-e2e --kubeconfig /tmp/agentbox-kubeconfig
```

---

## A. API conformance

Does the API server accept, reject and store what the schemas say it should?

| ID | Case | Expected | |
|---|---|---|---|
| A1 | Apply `crds/` to an empty cluster | 16 CRDs reach `Established` | ✅ |
| A2 | `kubectl api-resources --categories=agentbox` | All 16 kinds discoverable, correct short names | ✅ |
| A3 | Apply every reference example | Accepted and readable back at `v1beta1` | ✅ |
| A4 | Omit a defaulted field | API server fills it (`endpoints[].interface`, `health.periodSeconds`) | ✅ |
| A5 | Send an unknown field with strict validation | Rejected with the field named | ✅ |
| A6 | Send an unknown field with `--validate=ignore` | Pruned silently, not stored | ✅ |
| A7 | Omit a required field | Rejected per kind | ✅ |
| A8 | Send an invalid enum value | Rejected | ✅ |
| A9 | Violate the image-reference pattern | Rejected | ✅ |
| A10 | Port outside 1–65535 | Rejected | ✅ |
| A11 | Invalid cron expression | Rejected by pattern | ✅ |
| A12 | `metadata.name` longer than 253 chars or not DNS-1123 | Rejected | ✅ |
| A13 | Semver pattern on version fields | Rejected when malformed | ✅ |
| A14 | Apply an object with an explicit wrong `apiVersion` | Rejected | ✅ |
| A15 | `kubectl explain harnessruntime.spec` | Descriptions render for every field | ✅ |
| A16 | Printer columns render | `kubectl get` shows the declared columns for each kind | ✅ |
| A17 | Status subresource accepts a write | Status persists, spec untouched | ✅ |
| A18 | Spec write leaves status intact | Status survives | ✅ |
| A19 | `kubectl scale` on HarnessRuntime, ToolServer, Model | `spec.replicas` changes | ✅ |
| A20 | Server-side apply from two field managers | No ownership conflict on unrelated fields | ⬜ |
| A21 | `kubectl diff` on an unchanged object | Empty | ⬜ |

### CEL validation rules

| ID | Case | Expected | |
|---|---|---|---|
| A22 | `runtimeKind: cron` without `schedule` | Rejected | ✅ |
| A23 | `runtimeKind: server` without endpoints | Rejected | ✅ |
| A24 | `health.type: exec` without `command` | Rejected | ✅ |
| A25 | AutoScaler pointed at the wrong kind (all three) | Rejected | ✅ |
| A26 | `maxReplicas` below `minReplicas` | Rejected | ✅ |
| A27 | `type: aiMetric` scaling metric without `metric` | Rejected | ✅ |
| A28 | Dataset `config` variant not matching `type` | Rejected | ✅ |
| A29 | Two Dataset config variants set at once | Rejected | ✅ |
| A30 | Duplicate tool names in one ToolServer | Rejected | ✅ |
| A31 | `execution.mode: scheduled` without a schedule | Rejected | ✅ |
| A32 | `window.type: billingPeriod` without `period` | Rejected | ✅ |
| A33 | Tumbling window without `durationSeconds` | Rejected | ✅ |
| A34 | `pricing.model: tiered` without `tiers` | Rejected | ✅ |
| A35 | Zero or two Evaluator dataset sources | Rejected | ✅ |
| A36 | `Model.serving` without an image | Rejected | ✅ |
| A37 | Gateway `serving` without an image | Rejected | ✅ |
| A38 | Recipe with no stages | Rejected | ✅ |

---

## B. Reconciliation, per kind

Does the controller build the right thing, own it, and say so?

| ID | Case | Expected | |
|---|---|---|---|
| B1 | HarnessRuntime `server` | Deployment + Service, `status.endpoints` populated | ✅ |
| B2 | HarnessRuntime `worker` | Deployment, no Service | ✅ |
| B3 | HarnessRuntime `batch` | Job, no Deployment | ✅ |
| B4 | HarnessRuntime `cron` | CronJob carrying the declared schedule | ✅ |
| B5 | HarnessRuntime with GPU compute | Deployment requests `nvidia.com/gpu` | ⬜ |
| B6 | HarnessRuntime with `env` | Env vars land on the container | ✅ |
| B7 | HarnessRuntime health `http`/`tcp`/`exec` | Correct probe shape on the pod | ⬜ |
| B8 | ToolServer | Deployment + Service + catalog ConfigMap | ✅ |
| B9 | ToolServer catalog | Each tool has a resolvable URL and its contract | ✅ |
| B10 | ToolServer with many tools | All appear in the catalog and `status.tools` | ⬜ |
| B11 | Model without `serving` | Registry entry, nothing runs, no `status.address` | ✅ |
| B12 | Model with `serving` | Deployment + Service, `status.address` correct | ✅ |
| B13 | Model `serving.nodeSelector` | Pods pinned to the GPU node group | ✅ |
| B14 | Gateway without `serving` | Routing ConfigMap only | ✅ |
| B15 | Gateway with `serving` | Deployment + Service + config | ✅ |
| B16 | Gateway config content | Valid LiteLLM `model_list`, `apiKey` excluded | 🔶 (structure not asserted) |
| B17 | TrainLoop scheduled | CronJob | ✅ |
| B18 | TrainLoop unscheduled | Job | ✅ |
| B19 | TrainLoop `lifecycle.restartPolicy.maxRestarts` | Becomes the Job `backoffLimit` | ✅ |
| B20 | Evaluator | Suite ConfigMap with dataset and scoring | ✅ |
| B21 | Evaluator run annotation | Exactly one Job, `status.lastRunId` set | ✅ |
| B22 | Same run id reapplied | No second Job | ✅ |
| B23 | New run id | A second Job with the new name | ✅ |
| B24 | Evaluator run without `runConfig.image` | `failed` state, warning event, no Job | ✅ |
| B25 | AgentIdP | ServiceAccount + Role + RoleBinding | ✅ |
| B26 | AgentIdP `identity[].annotations` | Projected onto the ServiceAccount (IRSA-style) | ⬜ |
| B27 | AIMetric | Recording-rule ConfigMap, `status.currentValue` when available | ✅ |
| B28 | AIMetric `metricMath` type | Expression lands in the rule | ⬜ |
| B29 | Tracer | Collector config with the declared resource attributes | ✅ |
| B30 | Dataset | Connector ConfigMap, `status.address` derived per connector type | ✅ |
| B31 | Dataset, each of the 9 connector types | Address derived correctly for each | ✅ |
| B32 | Recipe | Stages topologically sorted into `status.resolvedOrder` | ✅ |
| B33 | Recipe with a dependency cycle | `failed`, message names the cycle | ✅ |
| B34 | Recipe with an unknown dependency | `failed`, message names it | ✅ |
| B35 | Recipe with duplicate stage ids | `failed` | ✅ |
| B36 | Every child has an owner reference | Controller-owned, blockOwnerDeletion | ✅ |
| B37 | Delete a parent | Children garbage-collected | ✅ |
| B38 | Every kind writes conditions | `Ready` present with a reason | ✅ |
| B39 | `observedGeneration` tracks `metadata.generation` | Matches after reconcile | ✅ |
| B40 | Reconcile twice with no change | No spurious writes, no failures | ✅ |

---

## C. Scaling

| ID | Case | Expected | |
|---|---|---|---|
| C1 | Metric 5× over target | Replicas scale by the HPA formula | ✅ |
| C2 | Metric under target | Scales down | ✅ |
| C3 | Metric within 10% of target | No change | ✅ |
| C4 | Metric at exactly the target | No change | ✅ |
| C5 | Two metrics disagreeing | Highest demand wins | ✅ |
| C6 | Demand above `maxReplicas` | Clamped to the ceiling | ✅ |
| C7 | Demand below `minReplicas` | Clamped to the floor | ✅ |
| C8 | `scaleToZero.enabled`, no demand | Reaches 0 | ✅ |
| C9 | Demand returns at 0 replicas | Activates 1, then scales normally | ✅ |
| C10 | Scale-up stabilization window | Blocks the next scale up, status says so | ✅ |
| C11 | Scale-down stabilization window | Blocks the next scale down | ✅ |
| C12 | `behavior.scaleUp.policies` rate limits | Step size respected | ✅ |
| C13 | Autoscaler target missing | `degraded`, no crash | ✅ |
| C14 | Autoscaler `enabled: false` | `suspended`, target untouched | ✅ |
| C15 | No metric value available | `pending`, target untouched | ✅ |
| C16 | Scaled replica count reaches the Deployment | Deployment follows `spec.replicas` | ✅ |
| C17 | All three autoscaler kinds scale their target | Model, HarnessRuntime, ToolServer | ✅ |
| C18 | `utilization` metric from `metrics.k8s.io` | Reads real pod CPU/memory | ⬜ |
| C19 | Manual `kubectl scale` while an autoscaler owns the target | Autoscaler reasserts on next reconcile | ✅ |
| C20 | Two autoscalers on one target | Documented behaviour, ideally rejected | ⬜ |

---

## D. Metering and budgets

| ID | Case | Expected | |
|---|---|---|---|
| D1 | Per-unit pricing | `currentCost` = usage / perUnits × unitPrice | ✅ |
| D2 | Tiered pricing | Correct cost across tier boundaries | ✅ |
| D3 | Flat pricing | Fixed cost | ✅ |
| D4 | No pricing block | Usage reported, cost absent | ✅ |
| D5 | Budget under limit | `budgetUsedPercent`, `budgetExceeded: false` | ✅ |
| D6 | Budget exceeded | `budgetExceeded: true` + `BudgetExceeded` event | ✅ |
| D7 | Alert thresholds crossed | `BudgetThreshold` event per threshold | ✅ |
| D8 | `limitType: usage` rather than cost | Budget measured in units | ✅ |
| D9 | Meter with no metric value | `pending`, no false zero | ✅ |
| D10 | Meter `enabled: false` | `suspended` | ⬜ |
| D11 | Usage attributed across `dimensions` | Per-tenant breakdown | ✅ |
| D12 | Cost across a month-long window | Correct accumulation | ⬜ |

---

## E. Guardrails

| ID | Case | Expected | |
|---|---|---|---|
| E1 | Condition met | `triggered: true` + `GuardrailTripped` event | ✅ |
| E2 | Condition not met | `triggered: false` | ✅ |
| E3 | Trip then clear | `GuardrailCleared` event, no event storm | ✅ |
| E4 | `all` conditions, one false | Not triggered | ✅ |
| E5 | `any` conditions, one true | Triggered | ✅ |
| E6 | Missing metric value | `pending`, not a false negative | ✅ |
| E7 | `status: disabled` | `inactive`, never evaluates | ✅ |
| E8 | Each operator: gt, gte, lt, lte, eq, neq | Correct comparison | ✅ |
| E9 | Observations recorded in status | Metric, observed, threshold, operator | ✅ |
| E10 | Cooldown between firings | Respected | ✅ |

---

## F. Identity, secrets and security

| ID | Case | Expected | |
|---|---|---|---|
| F1 | Deny-by-default AgentIdP | Role grants read-only verbs | ✅ |
| F2 | Allow-by-default AgentIdP | Role grants writes | ✅ |
| F3 | Secret-looking fields via the Python managers | Stored in a Secret, absent from the ConfigMap | ✅ |
| F4 | `includeSecrets=True` | Value merged back | ✅ |
| F5 | camelCase credential names (`apiKey`, `clientSecret`) | Detected | ✅ (unit) |
| F6 | Non-credentials (`stateKey`, `totalTokens`) | Not extracted | ✅ (unit) |
| F7 | Gateway `apiKey` in a CRD object | Never written into the generated ConfigMap | ✅ |
| F8 | Controller RBAC is sufficient | No permission errors across a full reconcile | ✅ |
| F9 | Controller RBAC is minimal | Cannot touch unrelated resources | ⬜ |
| F10 | Agent ServiceAccount can read tool catalogs | Discovery works under the granted Role | ⬜ |
| F11 | Secret rotation | Updated value picked up | ⬜ |
| F12 | Namespace isolation | A controller scoped to one namespace ignores others | ⬜ |

---

## G. Lifecycle and failure modes

| ID | Case | Expected | |
|---|---|---|---|
| G1 | `enabled: false` on ToolServer | `suspended`, Deployment removed | ✅ |
| G2 | Re-enable | Deployment recreated | ✅ |
| G3 | TrainLoop `paused` / `stopped` | `suspended` | ✅ |
| G4 | AIMetric `inactive` | `inactive`, no rule published | ✅ |
| G5 | Update an image | Deployment rolls | ✅ |
| G6 | Update endpoints | Service ports change | ✅ |
| G7 | Delete a CR | Children removed | ✅ |
| G8 | Delete a child by hand | Controller recreates it | ✅ |
| G9 | Edit a child by hand | Controller reverts the drift | ✅ |
| G10 | Controller restart mid-reconcile | Converges, no duplicates | ✅ |
| G11 | Controller offline for an hour | Catches up on resync | ⬜ |
| G12 | Watch connection dropped | Reconnects, no missed changes | ✅ |
| G13 | API server briefly unavailable | Retries, no crash loop | ⬜ |
| G14 | CRD uninstalled while objects exist | Controller logs and continues | 🔶 |
| G15 | Reconciler raises on one object | Others still reconcile, `failed` status written | 🔶 |
| G16 | Two controller replicas | Lease-based leader election; the standby stays idle | ✅ |
| G17 | Namespace deleted underneath | No orphaned work, no crash | ⬜ |

---

## H. Cross-kind scenarios

The ones that look like a real day.

| ID | Scenario | Expected | |
|---|---|---|---|
| H1 | **Ship an agent**: Gateway + HarnessRuntime + ToolServer, agent calls a tool through the Service | Traffic flows end to end | ✅ |
| H2 | **Traffic spike**: load raises the queue metric, swarm autoscaler scales, load drops, it scales back | Replicas track demand | ✅ |
| H3 | **Idle overnight**: scale to zero, first request wakes it | Cold start works | ✅ (metric-driven) |
| H4 | **Budget breach**: tokens accumulate past the limit, meter flags it, guardrail trips, gateway throttles | Chain fires end to end | ✅ |
| H5 | **Swap providers**: change Gateway from self-hosted to Bedrock | Agent image unchanged, traffic moves | ⬜ |
| H6 | **Nightly fine-tune**: TrainLoop CronJob runs, Evaluator scores the result, Model updated | Pipeline completes | ⬜ |
| H7 | **New tool rollout**: add a tool to a ToolServer | Catalog updates, agents discover it without redeploy | ⬜ |
| H8 | **Tenant onboarding**: new AgentIdP + AIMeter, isolated from existing tenants | No cross-tenant leakage | ⬜ |
| H9 | **Model rollback**: revert `serving.image` | Previous version serves again | ⬜ |
| H10 | **Regional migration**: apply the whole namespace to a second cluster | Identical state comes up | ⬜ |
| H11 | **GitOps**: manage all objects through Argo/Flux | No perpetual diff from controller-written status | ⬜ |
| H12 | **Incident**: kill the model pods and watch status | `degraded` reported accurately | ⬜ |

---

## I. Scale and performance

| ID | Case | Target | |
|---|---|---|---|
| I1 | 100 objects of one kind | Full resync under 30s | ⬜ |
| I2 | 1,000 objects across all kinds | Reconcile loop keeps up, memory stable | ⬜ |
| I3 | 50 namespaces | Cluster-scoped watch scales | ⬜ |
| I4 | Rapid edits to one object | No thrash, last write wins | ⬜ |
| I5 | Controller memory over 24h | No leak | ⬜ |
| I6 | Reconcile latency p50/p99 | Recorded and bounded | ⬜ |
| I7 | Prometheus query latency under load | Metric lookups do not stall the loop | ⬜ |
| I8 | 100 tools in one ToolServer | Catalog stays under the ConfigMap 1MB limit | ⬜ |

---

## J. Compatibility and upgrade

| ID | Case | Expected | |
|---|---|---|---|
| J1 | Kubernetes 1.25 → 1.31+ | CRDs install, CEL rules enforced on all | ⬜ |
| J2 | Reapply CRDs over existing objects | No data loss | ⬜ |
| J3 | Add a field to a schema, regenerate, reapply | Existing objects still valid | ⬜ |
| J4 | `generate_crds.py --check` in CI | Fails on stale manifests | ✅ |
| J5 | `generate_crd_docs.py --check` in CI | Fails on stale docs | ✅ |
| J6 | Registry ↔ schema parity | Every group has a schema declaring its kind | ✅ |
| J7 | Python 3.9 and 3.12 | Both pass the unit suite | ✅ (CI) |
| J8 | EKS, GKE, kind | CRDs and controller behave identically | 🔶 (kind only) |
| J9 | Old `v1alpha1` object applied | Rejected clearly | ⬜ |

---

## K. Real-workload integration

Nothing here runs a real model yet; every image in the suite is `busybox`.

| ID | Case | Expected | |
|---|---|---|---|
| K1 | Model serving a real vLLM image | `/v1/models` responds through the Service | ✅ |
| K2 | Gateway running real LiteLLM with the generated config | Proxies a completion | ✅ |
| K3 | Agent image calling a tool from the catalog | Tool executes and returns | ✅ |
| K4 | Real Prometheus as the metric source | Autoscaler scales on a real query | ⬜ |
| K5 | Real OTel Collector using the Tracer config | Traces arrive | ⬜ |
| K6 | Dataset against real Kafka / S3 / Postgres | Connector config is usable as published | ⬜ |
| K7 | Evaluator running a real scoring job | Scores written back | ⬜ |
| K8 | GPU node group with a real accelerator | Model schedules and serves | ⬜ |

---

## Priorities

The first sweep is done: drift correction, the watch loop, leader election, scale-down and
clamping, rate-limit policies, every pricing model, every guardrail operator and the
Dataset connector variants are all automated now, and closing them surfaced four real bugs
(scale-to-zero could not reach zero, could not wake from zero, and the `fs` and `database`
connectors read fields that did not exist).

What is left, in the order I would take it:

1. **K4–K8 — the rest of the real stack.** `examples/demo.yaml` proves the loop with real
   processes, but the model, gateway and tools are small stand-ins. Next: real vLLM, real
   LiteLLM, real Prometheus, a real GPU node group.
2. **G11/G13 — controller offline and API-server flapping.** The watch loop is tested;
   its behaviour under a broken API server is not.
3. **I1–I6 — scale.** No test runs more than about forty objects.
4. **F9/F10 — RBAC minimality.** The suite runs the controller as cluster-admin, so the
   ClusterRole in `deploy/` is asserted by inspection, not by use.
5. **J1/J8 — other Kubernetes versions and distributions.** kind only, one version.
