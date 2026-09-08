# Stage 2: Implementing OpenTelemetry Observability

This is the "before → after" exercise. Stage 1 (everything under `services/` and `deploy/`)
runs with **zero** observability — no OTel env vars, no SDKs, no exporters anywhere. This guide
walks through instrumenting it end-to-end, manually (no `opentelemetry-instrument` auto-agent,
no `opentelemetry-instrumentation-*` auto-instrumentor libraries) — you write every span,
every metric, every context-propagation call yourself. That's slower than auto-instrumentation,
but it's the point: auto-instrumentation is exactly this code, written for you, and it stops
being magic once you've written it once by hand.

Work through the steps in order. Each has a checkpoint — don't move on until it passes.

**A note on chart versions**: the Helm values below are illustrative of the shape, not
copy-paste-exact — SigNoz's and the OTel Collector's chart schemas evolve. When you reach a
step, check the current values against:
- SigNoz: https://signoz.io/docs/install/kubernetes/ and `helm show values signoz/signoz`
- Collector: https://github.com/open-telemetry/opentelemetry-helm-charts/tree/main/charts/opentelemetry-collector

---

## Step 1 — Deploy SigNoz

New namespace: `observability`. Add `apps/learn-otel-signoz.yaml` to the `Homelab` repo (same
app-of-apps convention as `freshrss`/`torrserver` — a `valuesObject`-based `Application`), or
`helm install` by hand first if you'd rather iterate on values before committing them.

Resource reality check: this is a 2-node arm64 Pi cluster with ~16GB RAM total, already running
your homelab workloads plus stage 1's ~1GB. SigNoz's own docs target production scale (16 vCPU/
32GB for ClickHouse alone) — nowhere close to what you have. That's fine for a low-traffic
learning workload, but only if you cap things explicitly instead of trusting chart defaults:

- Single replica everywhere (query-service, frontend, alertmanager, ClickHouse) — turn off any
  chart-default HA/replica-count>1.
- ClickHouse hard-capped: ~1Gi request / 3Gi limit, ~500m/1500m CPU. Use `clickhouse-keeper`
  (embedded) rather than a separate Zookeeper — one less stateful component to run.
- Short retention: 3 days traces/logs, 7 days metrics. Set this in ClickHouse's TTL config, not
  left at chart defaults (which usually assume weeks).
- Storage: use `nfs-client` (the only StorageClass you have) but watch ClickHouse's stability —
  NFS's fsync characteristics aren't a great match for ClickHouse's write pattern. Acceptable at
  this data volume; if you see write stalls or corruption, that's the first thing to suspect.

```yaml
# apps/learn-otel-signoz.yaml (in the Homelab repo)
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: learn-otel-signoz
  namespace: argocd
  finalizers: ["resources-finalizer.argocd.argoproj.io"]
spec:
  project: default
  destination:
    server: https://kubernetes.default.svc
    namespace: observability
  source:
    repoURL: https://charts.signoz.io
    chart: signoz
    targetRevision: "<check latest>"
    helm:
      valuesObject:
        clickhouse:
          replicaCount: 1
          persistence:
            storageClass: nfs-client
            size: 5Gi
          resources:
            requests: { cpu: 500m, memory: 1Gi }
            limits: { cpu: "1500m", memory: 3Gi }
        queryService:
          replicaCount: 1
        frontend:
          replicaCount: 1
        alertmanager:
          replicaCount: 1
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: ["CreateNamespace=true"]
```

Expose the UI the same way `frontend-gateway` is exposed — an `HTTPRoute` at `signoz.cluster.home`
against the `cilium` Gateway.

**Checkpoint**: `kubectl get pods -n observability` all Running, `https://signoz.cluster.home`
loads the UI, no data yet (nothing's sending telemetry).

---

## Step 2 — Deploy the OTel Collector tier

Two Collector releases, both via the `open-telemetry/opentelemetry-collector` Helm chart, both
in `observability`:

1. **Gateway** (`mode: deployment`, 1 replica): receives OTLP from the agent tier, batches, and
   forwards to SigNoz's OTLP ingest endpoint. Build and verify this one first, in isolation.
2. **Agent** (`mode: daemonset`): runs on every node, receives OTLP from local pods (via each
   pod's `HOST_IP` or a `hostPort`), forwards to the gateway Service. Deploy only after the
   gateway is verified working.

```yaml
# gateway collector values sketch
mode: deployment
replicaCount: 1
resources:
  requests: { cpu: 100m, memory: 128Mi }
  limits: { cpu: 500m, memory: 256Mi }
config:
  receivers:
    otlp:
      protocols: { grpc: {}, http: {} }
  processors:
    batch: {}
  exporters:
    otlp:
      endpoint: signoz-otel-collector.observability.svc.cluster.local:4317
      tls: { insecure: true }
  service:
    pipelines:
      traces: { receivers: [otlp], processors: [batch], exporters: [otlp] }
      metrics: { receivers: [otlp], processors: [batch], exporters: [otlp] }
      logs: { receivers: [otlp], processors: [batch], exporters: [otlp] }
```

(SigNoz ships its own Collector as part of the chart, listening on `4317`/`4318` inside the
`signoz-otel-collector` Service — check the actual Service name from `kubectl get svc -n
observability` once Step 1 is deployed, chart versions rename this occasionally.)

Verify the gateway in isolation before touching any app code: `kubectl run` a throwaway pod with
`curl`/`grpcurl`, or use the [otel-cli](https://github.com/equinix-labs/otel-cli) project to fire
one manual span at the gateway's Service and confirm it shows up in SigNoz.

Then deploy the agent DaemonSet, pointed at the gateway Service (`otlp.observability.svc.cluster.local:4317`
or similar — name it per your Helm release), with `mode: daemonset` and the same receiver/exporter
shape but exporting to the gateway instead of directly to SigNoz.

**Checkpoint**: a manually-fired test span reaches SigNoz via agent → gateway → SigNoz collector
→ ClickHouse, visible in the SigNoz UI's trace explorer.

---

## Step 3 — Instrument `order-service` traces first

Add a shared bootstrap to `libs/common/otel_common/otel.py` (new file):

```python
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter


def configure_tracing(service_name: str, otlp_endpoint: str) -> None:
    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "learn-otel",
        "deployment.environment": "home",
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
```

Call `configure_tracing(settings.service_name, settings.otlp_endpoint)` once at process startup
(top of `app/main.py`, before the FastAPI app object is created), where `otlp_endpoint` points at
the local Collector **agent** (not the gateway, not SigNoz directly — apps always talk to the
node-local agent).

Since there's no framework auto-instrumentor, write a small ASGI middleware that manually starts
a SERVER span per request, extracting whatever trace context arrived on the incoming headers:

```python
from starlette.middleware.base import BaseHTTPMiddleware
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind

class TracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        ctx = extract(request.headers)
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(
            f"{request.method} {request.url.path}", context=ctx, kind=SpanKind.SERVER
        ) as span:
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.route", request.url.path)
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
            return response
```

`app.add_middleware(TracingMiddleware)`. Writing this by hand is exactly what
`FastAPIInstrumentor` does for you — now you know how.

Wrap the outbound call to `pricing-service` in a manual CLIENT span, injecting the current
context onto the outgoing headers:

```python
from opentelemetry.propagate import inject

async def call_pricing_service(client, pricing_request):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("POST /calculate", kind=SpanKind.CLIENT) as span:
        span.set_attribute("http.method", "POST")
        span.set_attribute("peer.service", "pricing-service")
        headers = {}
        inject(headers)
        resp = await client.post("/calculate", json=pricing_request.model_dump(), headers=headers)
        span.set_attribute("http.status_code", resp.status_code)
        return resp
```

And wrap the Postgres query in a manual span with `db.*` attributes:

```python
with tracer.start_as_current_span("INSERT orders", kind=SpanKind.CLIENT) as span:
    span.set_attribute("db.system", "postgresql")
    span.set_attribute("db.name", "orders")
    span.set_attribute("db.statement", "INSERT INTO orders ...")
    # ... existing asyncpg call ...
```

**Checkpoint**: place an order, see a single `order-service` trace in SigNoz with the request
span, the pricing-service CLIENT span (not yet connected to anything on the pricing-service
side — that's Step 4), and the DB span nested underneath it.

---

## Step 4 — Instrument the rest of the sync chain

Repeat the exact same three ingredients — `configure_tracing()` at startup, the
`TracingMiddleware` for inbound spans, manual CLIENT spans with `inject()` for outbound calls —
in `pricing-service`, `catalog-service`, and `frontend-gateway`. `pricing-service` and
`catalog-service` only need the middleware (they don't call anything else synchronously except
Postgres/Redis, which get the same manual CLIENT-span treatment as `order-service`'s DB call).
`frontend-gateway` needs both the middleware and manual CLIENT spans for its calls to
`catalog-service` and `order-service`.

**Checkpoint**: one request through `frontend-gateway` produces a single trace with correctly
nested spans: `frontend-gateway` → `catalog-service` (menu lookup), `frontend-gateway` →
`order-service` → `pricing-service`, all as one waterfall in the SigNoz trace view. Trigger a
`pricing-service` artificial error or slowdown (it happens ~2-3%/~8% of requests on its own) and
confirm it's visible as a red span / a long span in the waterfall.

---

## Step 5 — Propagate trace context across the NATS hop

This is the one place nothing gives you propagation for free — NATS has no built-in tracing
convention, so this is entirely hand-rolled, and it's the most valuable lesson in this guide.

**On publish** (in `order-service`, when it publishes `order.created`, and in `order-service`'s
status-update handler when it publishes `order.ready`/`order.delivered`), inject the current
context into NATS message headers:

```python
from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind
import nats

async def publish_event_traced(js, subject, payload):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(f"{subject} publish", kind=SpanKind.PRODUCER) as span:
        span.set_attribute("messaging.system", "nats")
        span.set_attribute("messaging.destination", subject)
        headers = nats.aio.msg.Msg.Headers = {}
        inject(headers)
        await js.publish(subject, payload.model_dump_json().encode(), headers=headers)
```

**On consume** (`kitchen-service`, `delivery-service`, `notification-service`), extract the
context from `msg.headers` and decide how to relate the new span to the publisher's trace. Two
options, both valid, pick deliberately:

**Option A — new trace, linked to the parent (recommended, matches OTel messaging semantic
conventions for fan-out/fire-and-forget):**

```python
from opentelemetry.trace import Link

async def handle_order_created(msg):
    parent_ctx = extract(msg.headers or {})
    parent_span_ctx = trace.get_current_span(parent_ctx).get_span_context()
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(
        "order.created process", links=[Link(parent_span_ctx)], kind=SpanKind.CONSUMER
    ) as span:
        ...
```
This produces a *separate* trace per consumer, each carrying a link back to the publish event —
correct because `order.created` genuinely fans out to two independent, unrelated pieces of work
(cooking, notifying) that don't causally block or belong to the original HTTP request's latency
budget.

**Option B — continue the same trace:**

```python
with tracer.start_as_current_span("order.created process", context=parent_ctx, kind=SpanKind.CONSUMER) as span:
    ...
```
Simpler, gives you one single waterfall from HTTP request all the way through cooking and
delivery — genuinely nice for a learning demo where you want to *see* the whole order lifecycle
in one view. Semantically it overstates the causal relationship (the HTTP response already
returned before cooking finishes), but for a learning exercise this trade-off is worth trying
both ways and comparing what each looks like in the SigNoz trace view.

**Checkpoint**: place an order, find the `order.created` publish span, and (depending on which
option you picked) either see linked-but-separate traces for kitchen/notification, or one
continuous trace spanning the whole order lifecycle. Try both — that comparison is the lesson.

---

## Step 6 — Repeat across the remaining services and hops

Apply Step 5's pattern to `delivery-service` (consuming `order.ready`, publishing nothing new
itself — it PATCHes `order-service`, which publishes `order.delivered`) and to
`notification-service`'s three consumers (`order.created`, `order.ready`, `order.delivered`).
By now this should be mechanical — same middleware, same manual CLIENT/PRODUCER/CONSUMER span
pattern, no new concepts.

**Checkpoint**: a full order lifecycle — create → price → cook → ready → deliver → three
notifications — is fully traced end to end.

---

## Step 7 — Add metrics

Same `Resource` as tracing. Add to `otel_common/otel.py`:

```python
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter


def configure_metrics(service_name: str, otlp_endpoint: str) -> None:
    resource = Resource.create({"service.name": service_name, "service.namespace": "learn-otel"})
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True))
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
```

Custom metrics worth adding (one or two per service, not exhaustive coverage):
- `order-service`: `orders_created_total` (counter), `order_processing_duration_seconds`
  (histogram, measured from `order.created` publish to `order.delivered` — you'll need to thread
  a start timestamp through, e.g. as an order field or a metric recorded in the
  `order.delivered` handler using the order's `created_at`).
- `pricing-service`: `pricing_calls_slow_total`, `pricing_calls_failed_total` (counters,
  incremented right where the artificial slow/error branches already are).
- `kitchen-service` / `delivery-service` / `notification-service`: `queue_messages_consumed_total`
  (counter, labeled by `subject`).

Don't hand-roll CPU/memory metrics — the Collector's `hostmetrics` receiver (add it to the agent
DaemonSet's pipeline from Step 2) gives you node/pod-level infra metrics for free.

**Checkpoint**: SigNoz's metrics explorer shows `orders_created_total` climbing as the load
generator runs.

---

## Step 8 — Add logs

```python
import logging
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter


def configure_logging_otel(service_name: str, otlp_endpoint: str) -> None:
    resource = Resource.create({"service.name": service_name, "service.namespace": "learn-otel"})
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=otlp_endpoint, insecure=True)))
    logging.getLogger().addHandler(LoggingHandler(logger_provider=provider))
```

Call this alongside `configure_tracing`/`configure_metrics` at startup. The OTel logging bridge
automatically stamps `trace_id`/`span_id` onto any log record emitted while a span is active — no
manual injection needed, which is why this comes last: there'd be nothing to correlate against
until traces existed.

**Checkpoint**: open a trace in SigNoz, check its "Logs" tab — the log lines your handlers
already emit (via `configure_logging` in `otel_common/logging.py`) show up correlated to that
exact request/span.

---

## Step 9 — Dashboards & alerts

Build in the SigNoz UI:
- **Order funnel latency**: p50/p95/p99 of `order_processing_duration_seconds`.
- **Error rate by service**: derived from span status codes, one panel per service.
- **Queue lag**: JetStream consumer lag — check what NATS/JetStream metrics the Collector's
  receivers can pull, or track it manually via `queue_messages_consumed_total` vs. a publish
  counter.

Alerts (1-2 is enough to close the loop from signal to action):
- `pricing-service` error rate above ~5% over 5 minutes.
- Order-processing p95 above some threshold (e.g. 15s).

---

## Step 10 — Cleanup pass

- Standardize resource attributes across every service: `service.name`, `service.namespace`,
  `service.version`, `deployment.environment` — inject via `OTEL_RESOURCE_ATTRIBUTES` set per
  Deployment in each `deploy/base/<service>/deployment.yaml` (Kustomize), not hardcoded in
  Python, so the same image can run with different resource attributes per environment later.
- Review attribute names against [OTel semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
  — `http.*`, `messaging.*`, `db.*` — the manual spans above use a reasonable subset; the real
  convention sets are larger (e.g. `http.request.method`, `server.address` in newer semconv
  versions — check which version your OTLP exporter/Collector pipeline assumes).
- Strip any `ConsoleSpanExporter`/debug exporters you added while testing Steps 3-8 — OTLP-only
  in the final state.
