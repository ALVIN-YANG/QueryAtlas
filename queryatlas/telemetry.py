"""Five-minute service window, aggregated into ten-second buckets."""

import math
import time
from collections import deque
from threading import Lock

LIMITS = (0.1, 0.5, 1, 3, 5, 10, 15, 30, float("inf"))
buckets = deque(maxlen=30)
lock = Lock()


def elapsed_ms(start):
    return round((time.perf_counter() - start) * 1000, 2)


def record(status, elapsed):
    slot = int(time.monotonic() // 10)
    with lock:
        if not buckets or buckets[-1][0] != slot:
            buckets.append((slot, [0] * len(LIMITS), [0]))
        _, hist, failures = buckets[-1]
        hist[next(i for i, v in enumerate(LIMITS) if elapsed <= v)] += 1
        failures[0] += int(status >= 500)


def window():
    first = int(time.monotonic() // 10) - 29
    with lock:
        hist = [0] * len(LIMITS)
        errors = 0
        for slot, h, failures in buckets:
            if slot < first:
                continue
            hist = [a + b for a, b in zip(hist, h)]
            errors += failures[0]
    count = sum(hist)
    remaining = math.ceil(count * 0.95)
    p95 = None
    if count:
        for bound, freq in zip(LIMITS, hist):
            remaining -= freq
            if remaining <= 0:
                p95 = ">30" if math.isinf(bound) else bound
                break
    return {"samples": count, "errors": errors, "p95_upper_s": p95}
