from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
import json
import logging
import math
import urllib.error
import urllib.request

from .config import CONFIG
from .retry import RequestFailure, run_with_retry

logger = logging.getLogger(__name__)


def parse_retry_after(value, *, now=None):
    if not value:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - (now or datetime.now(timezone.utc))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def validate_response(data):
    try:
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("assistant message required")
        if choice.get("finish_reason") not in {"stop", "tool_calls"}:
            raise RequestFailure("incomplete_response")
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ValueError("tool_calls must be a list")
        if (choice['finish_reason'] == 'tool_calls') != bool(calls):
            raise ValueError('finish_reason does not match tool calls')
        identifiers = set()
        for call in calls:
            if not isinstance(call["id"], str) or not call["id"] or call["id"] in identifiers:
                raise ValueError("tool call IDs must be nonempty and unique")
            identifiers.add(call["id"])
            function = call["function"]
            if not isinstance(function["name"], str) or not function["name"]:
                raise ValueError("tool name required")
            arguments = function["arguments"]
            if not isinstance(arguments, str) or not isinstance(json.loads(arguments), dict):
                raise ValueError("tool arguments must encode an object")
        if message.get("content") is not None and not isinstance(message["content"], str):
            raise ValueError("text content must be a string or null")
        if not calls and message.get("content") is None:
            raise ValueError("response contains neither text nor tool calls")
    except (KeyError, IndexError, TypeError, ValueError):
        raise RequestFailure("invalid_response") from None
    return data


def post_json(request, *, signal=None, opener=None):
    opener = opener or urllib.request.urlopen

    def once():
        try:
            with opener(request, timeout=CONFIG["request_timeout_s"]) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            try:
                retry_after = parse_retry_after(error.headers.get("Retry-After") if error.headers else None)
                failure = RequestFailure("http_error", status=error.code, retry_after=retry_after)
            finally:
                error.close()
            raise failure from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            raise RequestFailure("outcome_unknown") from None
        try:
            return validate_response(json.loads(payload.decode("utf-8")))
        except (UnicodeError, json.JSONDecodeError):
            raise RequestFailure("invalid_response") from None

    def report(number, status, delay):
        logger.warning("Model request retry %s/%s; HTTP %s; waiting %.2fs", number,
                       CONFIG["request_max_retries"], status, delay)

    return run_with_retry(
        once, max_retries=CONFIG["request_max_retries"],
        base_delay=CONFIG["request_retry_base_s"], max_delay=CONFIG["request_retry_cap_s"],
        signal=signal, on_retry=report,
    )
