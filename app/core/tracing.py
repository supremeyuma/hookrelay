"""OpenTelemetry tracing setup."""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)


def configure_tracing(
    *,
    service_name: str,
    otlp_endpoint: str | None,
    console: bool,
) -> TracerProvider:
    """Install the tracer provider and global client instrumentors."""
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))

    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
    elif console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    RedisInstrumentor().instrument(tracer_provider=provider)
    AsyncPGInstrumentor().instrument(tracer_provider=provider)  # type: ignore[no-untyped-call]
    return provider
