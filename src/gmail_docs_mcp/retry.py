"""Bounded retry with exponential backoff and jitter for transient Google API errors."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from googleapiclient.errors import HttpError

from .errors import http_status, is_rate_limited

T = TypeVar("T")

MAX_ATTEMPTS = 3
RATE_LIMIT_ONLY = frozenset({429})
TRANSIENT = frozenset({429, 500, 503})


def call_with_retry(
    fn: Callable[[], T],
    *,
    retry_statuses: frozenset[int] = TRANSIENT,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Call ``fn`` and retry only on ``HttpError`` statuses listed in ``retry_statuses``.

    A 403 carrying a rate-limit reason is treated as a 429. Non-HTTP errors (timeouts, network
    failures) are never retried: for non-idempotent calls they are ambiguous.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except HttpError as exc:
            attempt += 1
            status = http_status(exc)
            retryable = status in retry_statuses or (429 in retry_statuses and is_rate_limited(exc))
            if not retryable or attempt >= max_attempts:
                raise
            delay = min(8.0, 0.5 * 2 ** (attempt - 1)) * (0.5 + jitter())
            sleep(delay)
