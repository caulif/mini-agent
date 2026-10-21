import math
import random
import time


class RequestFailure(RuntimeError):
    def __init__(self, category, *, status=None, retry_after=None):
        self.category = category
        self.status = status
        self.retry_after = retry_after
        labels = {
            "http_error": "Server returned an HTTP error",
            "outcome_unknown": "No usable response; server outcome is unknown",
            "invalid_response": "Invalid response structure or JSON; no tools executed",
            "incomplete_response": "Response did not finish normally; no tools executed",
        }
        message = labels.get(category, category)
        if status is not None:
            message += f"（HTTP {status}）"
        super().__init__(message)

    @property
    def retryable(self):
        return self.category == "http_error" and self.status in {429, 502, 503, 504}


def wait_for_retry(seconds, signal=None):
    end = time.monotonic() + seconds
    while True:
        if signal is not None:
            signal.check()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def run_with_retry(operation, *, max_retries=2, base_delay=0.5, max_delay=10.0,
                   signal=None, wait=None, random_value=None, on_retry=None):
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 10:
        raise ValueError("max_retries must be an integer from 0 to 10")
    if not all(math.isfinite(x) and x >= 0 for x in (base_delay, max_delay)):
        raise ValueError("Retry delays must be finite and nonnegative")
    wait = wait or wait_for_retry
    random_value = random_value or random.random

    for attempt in range(max_retries + 1):
        if signal is not None:
            signal.check()
        try:
            result = operation()
        except RequestFailure as error:
            if not error.retryable or attempt == max_retries:
                raise
            delay = min(max_delay, base_delay * 2 ** attempt)
            delay *= 0.5 + 0.5 * random_value()
            if error.retry_after is not None:
                if error.retry_after > max_delay:
                    raise
                delay = max(delay, error.retry_after)
            if on_retry is not None:
                on_retry(attempt + 1, error.status, delay)
            wait(delay, signal)
        else:
            if signal is not None:
                signal.check()
            return result
