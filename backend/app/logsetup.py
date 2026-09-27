"""Logging configuration shared by the API and the worker."""
from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# The Azure SDK logs every queue and blob HTTP request and response at INFO. The worker
# polls the queue once a second, so this emits roughly 40 lines a minute and buries the
# handful of messages we actually care about ("claimed INV-0002", "analysis complete").
_NOISY_LOGGERS = (
    "azure.core.pipeline.policies.http_logging_policy",
)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
