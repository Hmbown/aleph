"""Optional OpenTelemetry helpers.

Aleph should not require OpenTelemetry to run, so these helpers fall back to
no-op spans when the package is unavailable.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

trace: Any

try:  # pragma: no cover - import availability depends on extras
    from opentelemetry import trace
    from opentelemetry.trace import Span
except Exception:  # pragma: no cover - fallback path
    trace = None
    Span = Any  # type: ignore[assignment,misc]


class _NoOpSpan:
    def set_attribute(self, name: str, value: Any) -> None:
        return None

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        return None

    def record_exception(self, exception: BaseException) -> None:
        return None


def _normalize_attr(value: Any) -> str | bool | int | float:
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


@contextmanager
def traced_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span | _NoOpSpan]:
    """Start an OpenTelemetry span when the dependency is installed."""

    if trace is None:  # pragma: no cover - exercised only when extra absent
        yield _NoOpSpan()
        return

    tracer = trace.get_tracer("aleph")
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                if value is None:
                    continue
                span.set_attribute(key, _normalize_attr(value))
        yield span
