# Stage 2: Implementing OpenTelemetry Observability

This is the "before → after" exercise.

Stage 1 (everything under `services/` and `deploy/`) runs with **zero** observability — no OTel env vars, no SDKs, no exporters anywhere.
This guide walks through instrumenting it end-to-end, manually (no `opentelemetry-instrument` auto-agent, no `opentelemetry-instrumentation-*` auto-instrumentor libraries) —
you write every span, every metric, every context-propagation call yourself. That's slower than auto-instrumentation, but it's the point: auto-instrumentation is exactly this code, written for you, and it stops being magic once you've written it once by hand.

Work through the steps in order. Each has a checkpoint — don't move on until it passes.

---

See the [Architecture diagram in the README](../README.md#architecture) for the full stage-1
service topology (sync HTTP calls vs. async NATS hops) before starting — every span/metric/log
you add from Step 3 onward corresponds to one of those arrows.

---

## Step 1 — Deploy SigNoz:

Add `apps/signoz.yaml` to ArgoCD application repo

```yaml
# apps/signoz.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: signoz
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  destination:
    server: https://kubernetes.default.svc
    namespace: observability
  source:
    repoURL: https://charts.signoz.io
    chart: signoz
    targetRevision: 0.141.1
    helm:
      valuesObject:
        clickhouse:
          replicaCount: 1
          zookeeper:
            replicaCount: 1
            resources:
              limits: {}
              requests:
                memory: 256Mi
                cpu: 100m
            persistence:
              storageClass: nfs-client
              size: 5Gi
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
            limits:
              cpu: 1500m
              memory: 3Gi
          persistence:
            storageClass: nfs-client
            size: 5Gi
        signoz:
          replicaCount: 1
          resources:
            requests:
              cpu: 100m
              memory: 100Mi
            limits:
              cpu: 750m
              memory: 1000Mi
          persistence:
            storageClass: nfs-client
            size: 1Gi
        otelCollector:
          resources:
            requests:
              cpu: 100m
              memory: 200Mi
            limits:
              cpu: "1"
              memory: 2Gi
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

Helm chart will install these components:
- Signoz Statefullset
- Clickhouse Operator > Clickhouse Cluster Statefullset
- Zookeeper Statefullset
- OTel collector

Next we should expose the signoz UI using `HTTPRoute` at `signoz.cluster.home`
Note: As of today, Signoz official Helm Chart doesn't support `HTTPRoute`, so it should be added separately

**Checkpoint**: `kubectl get pods -n observability` all Running, `https://signoz.cluster.home` loads the UI, no data yet (nothing's sending telemetry).

TODO: understand retention policies

---

## Step 2 — Deploy the OTel Collector tier

### Why not just point everything at SigNoz's own collector?

You already have one: `signoz-otel-collector`, running in `observability` as part of the SigNoz
chart. It *is* a real OpenTelemetry Collector — SigNoz doesn't fork or replace the project, they
ship one pre-configured with SigNoz-specific exporters that know how to write into their
ClickHouse schema (traces, logs, metrics tables, span-to-metrics processing). Its job is narrow
and fixed: be the ingestion front door for SigNoz specifically.

We're deploying two more Collectors ourselves — same binary, generic config, no backend opinion —
because in a real deployment applications almost never talk directly to your backend's ingestion
endpoint. They talk to *your own* Collector, which forwards wherever you point it. That's what
turns swapping backends, adding a second destination, sampling, filtering, or enriching with k8s
metadata into a Collector-config change instead of an application code change. Concretely:

- **Agent** (`mode: daemonset`, one pod per node): the thing every application pod on that node
  talks to. Local, fast, no cross-node hop, no coupling to SigNoz's address.
- **Gateway** (`mode: deployment`, centralized): where you'd put batching, retries, routing,
  fan-out to multiple backends. Right now it does one simple thing — forward to SigNoz — but the
  point is that it's a single place to change that later.

This agent → gateway → backend shape is OpenTelemetry's own standard reference architecture, not
something specific to this project.

```mermaid
flowchart LR
    subgraph Node["any k8s node"]
        APP["app pod"] -->|"OTLP :4317 via $(HOST_IP)"| AGENT["otel-collector-agent\n(DaemonSet, hostPort 4317)"]
    end
    AGENT -->|"OTLP :4317"| GW["otel-collector-gateway\n(Deployment)"]
    GW -->|"OTLP :4317"| SC["signoz-otel-collector\n(SigNoz's own Collector)"]
    SC --> CH[(ClickHouse)]
    CH --> UI[SigNoz UI]
```

Deploy the gateway first and verify it in isolation before the agent — it's the simpler of the two
to reason about on its own.

### Gateway

`apps/otel-collector-gateway.yaml` in Homelab, same flat style as `apps/signoz.yaml`:

```yaml
# apps/otel-collector-gateway.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: otel-collector-gateway
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  destination:
    server: https://kubernetes.default.svc
    namespace: observability
  source:
    repoURL: https://open-telemetry.github.io/opentelemetry-helm-charts
    chart: opentelemetry-collector
    targetRevision: 0.172.1
    helm:
      valuesObject:
        mode: deployment
        replicaCount: 1
        fullnameOverride: otel-collector-gateway
        resources:
          requests: { cpu: 100m, memory: 128Mi }
          limits: { cpu: 500m, memory: 256Mi }
        config:
          exporters:
            otlp:
              endpoint: signoz-otel-collector.observability.svc.cluster.local:4317
              tls:
                insecure: true
          service:
            pipelines:
              traces:
                exporters: [otlp]
              metrics:
                exporters: [otlp]
              logs:
                exporters: [otlp]
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

That's the whole override — the chart's own defaults already ship a full `otlp` receiver →
`memory_limiter, batch` → `<exporter>` pipeline for all three signals; we're only replacing the
default `debug` exporter with `otlp`, and only under `exporters:` (Helm's map-level merge means
`receivers`/`processors` are left exactly as the chart defaults them, untouched). With
`fullnameOverride` set, the resulting Service name is deterministic, not a guess:
`otel-collector-gateway.observability.svc.cluster.local:4317`.

**Verify the gateway in isolation** before touching the agent or any app code: run a throwaway
pod with `grpcurl`/`curl`, or use [otel-cli](https://github.com/equinix-labs/otel-cli), to fire
one manual span at `otel-collector-gateway.observability.svc.cluster.local:4317` and confirm it
shows up in the SigNoz UI's trace explorer. If it doesn't, debug this hop before adding the agent
— keep exactly one new variable at a time.

### Agent

`apps/otel-collector-agent.yaml`, same chart:

```yaml
# apps/otel-collector-agent.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: otel-collector-agent
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  destination:
    server: https://kubernetes.default.svc
    namespace: observability
  source:
    repoURL: https://open-telemetry.github.io/opentelemetry-helm-charts
    chart: opentelemetry-collector
    targetRevision: 0.172.1
    helm:
      valuesObject:
        mode: daemonset
        fullnameOverride: otel-collector-agent
        resources:
          requests: { cpu: 50m, memory: 64Mi }
          limits: { cpu: 200m, memory: 128Mi }
        config:
          exporters:
            otlp:
              endpoint: otel-collector-gateway.observability.svc.cluster.local:4317
              tls:
                insecure: true
          service:
            pipelines:
              traces:
                exporters: [otlp]
              metrics:
                exporters: [otlp]
              logs:
                exporters: [otlp]
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

No `ports:` override needed — the chart's defaults already set `hostPort: 4317`/`4318` on the
OTLP receiver ports, and that takes effect automatically the moment `mode: daemonset` is set. Each
node now has an agent pod listening on its own host IP, port 4317.

### How application pods actually reach the agent

This is the piece the earlier version of this guide skipped: a pod doesn't know its node's IP by
default, so use the Kubernetes Downward API to inject it, then build the OTLP endpoint from it.
Add to **every** stage-1 service's `deploy/base/<service>/deployment.yaml`, in the container's
`env:` block:

```yaml
- name: HOST_IP
  valueFrom:
    fieldRef:
      fieldPath: status.hostIP
- name: OTLP_ENDPOINT
  value: "http://$(HOST_IP):4317"
```

And add the matching field to each service's `Settings` class:

```python
class Settings(BaseServiceSettings):
    ...
    otlp_endpoint: str
```

This is what `settings.otlp_endpoint` in Step 3 below reads from — every app pod ends up pointed
at the agent running on its *own* node, never at the gateway or SigNoz directly.

**Checkpoint**: a manually-fired test span (from the gateway verification above) reaches SigNoz
via agent → gateway → `signoz-otel-collector` → ClickHouse, visible in the SigNoz UI's trace
explorer. Confirm `deploy/base/order-service/deployment.yaml` has `HOST_IP`/`OTLP_ENDPOINT` wired
before moving on to Step 3.

---

## Step 3 — Instrument `order-service` traces first

Add a shared bootstrap to `libs/common/app_common/otel.py` (new file):

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

Same `Resource` as tracing. Add to `app_common/otel.py`:

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
already emit (via `configure_logging` in `app_common/logging.py`) show up correlated to that
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
