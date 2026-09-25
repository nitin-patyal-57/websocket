# ============================================================
# LOGGING
# ============================================================
#
# Request IDs are propagated through a context variable so that every
# log line produced while handling one Soundbox request - including
# lines emitted deep inside audio/STT/AI/TTS modules - carries the
# same [req_XXX] prefix and can be traced end to end.

import contextvars
import logging

from . import config

# Current request ID for this task/thread context.
request_id_var = contextvars.ContextVar("request_id", default="-")

# Monotonic request counter (per process).
_request_counter = 0


class RequestIdFilter(logging.Filter):
    """Attach the current [req_XXX] id to every record."""

    def filter(self, record):
        if not hasattr(record, "req_id"):
            record.req_id = request_id_var.get()
        return True


def next_request_id():
    """Return the next request id, e.g. 'req_001'."""
    global _request_counter
    _request_counter += 1
    return f"req_{_request_counter:03d}"


def set_request_id(req_id):
    """Set the request id for the current context."""
    request_id_var.set(req_id)


def setup_logging():
    """Configure root logging once, with request-id support."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | [%(req_id)s] %(message)s")
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))

    return logging.getLogger("WS_AI_SERVER")
