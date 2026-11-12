"""Prometheus metrics shared by the API, worker, and poller processes."""

from prometheus_client import Counter, Gauge, Histogram

INGEST_TOTAL = Counter(
    "hookrelay_ingest",
    "Ingest requests by result",
    ["result"],
)
INGEST_DURATION = Histogram(
    "hookrelay_ingest_duration_seconds",
    "Time spent handling an ingest request",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

DELIVERY_ATTEMPTS = Counter(
    "hookrelay_delivery_attempts",
    "Delivery attempts by final result",
    ["result"],
)
DELIVERY_DURATION = Histogram(
    "hookrelay_delivery_duration_seconds",
    "Time spent on one delivery HTTP attempt",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
DLQ_TOTAL = Counter(
    "hookrelay_dlq",
    "Deliveries moved to the dead-letter table",
)

OUTBOX_LAG = Gauge(
    "hookrelay_outbox_lag_seconds",
    "Age of the oldest unpublished outbox row",
    multiprocess_mode="max",
)
EVENTS_BY_STATUS = Gauge(
    "hookrelay_events_by_status",
    "Current number of events by status",
    ["status"],
    multiprocess_mode="max",
)

POLLER_PUBLISHED = Counter(
    "hookrelay_poller_published",
    "Deliveries created by the outbox poller",
)
POLLER_ENQUEUED = Counter(
    "hookrelay_poller_enqueued",
    "Deliveries enqueued by the outbox poller",
)
POLLER_ERRORS = Counter(
    "hookrelay_poller_errors",
    "Poller ticks that failed",
)
