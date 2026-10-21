from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from . import agent, llm, transport
from .config import CONFIG, setup_console
from .control import Aborted, Signal
from .retry import RequestFailure, run_with_retry


@contextmanager
def response(data):
    yield io.BytesIO(json.dumps(data).encode("utf-8"))


def http_error(code, retry_after=None):
    return urllib.error.HTTPError("https://example.invalid", code, "test",
                                  {"Retry-After": retry_after} if retry_after else {}, io.BytesIO(b"private body"))


class RetryChecks(unittest.TestCase):
    def test_transient_recovers(self):
        calls, delays = [], []
        def operation():
            calls.append(1)
            if len(calls) < 3:
                raise RequestFailure("http_error", status=503)
            return "ok"
        result = run_with_retry(operation, wait=lambda d, s: delays.append(d), random_value=lambda: 1)
        self.assertEqual((result, len(calls), delays), ("ok", 3, [0.5, 1.0]))

    def test_budget_exhaustion(self):
        calls = []
        failure = RequestFailure("http_error", status=429)
        def operation():
            calls.append(1)
            raise failure
        with self.assertRaises(RequestFailure) as raised:
            run_with_retry(operation, wait=lambda d, s: None)
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(calls), 3)

    def test_permanent_not_retried(self):
        for status in (400, 401, 403, 404, 500):
            with self.subTest(status=status):
                calls = []
                def operation():
                    calls.append(1)
                    raise RequestFailure("http_error", status=status)
                with self.assertRaises(RequestFailure):
                    run_with_retry(operation, wait=lambda d, s: self.fail("不应等待"))
                self.assertEqual(len(calls), 1)

    def test_unknown_not_retried(self):
        failure = RequestFailure("outcome_unknown")
        with self.assertRaises(RequestFailure) as raised:
            run_with_retry(lambda: (_ for _ in ()).throw(failure), wait=lambda d, s: self.fail("不应等待"))
        self.assertIs(raised.exception, failure)

    def test_retry_after_minimum(self):
        script = [RequestFailure("http_error", status=429, retry_after=3), "ok"]
        delays = []
        def operation():
            value = script.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        run_with_retry(operation, wait=lambda d, s: delays.append(d), random_value=lambda: 0)
        self.assertEqual(delays, [3])

    def test_retry_after_too_long(self):
        failure = RequestFailure("http_error", status=429, retry_after=60)
        with self.assertRaises(RequestFailure):
            run_with_retry(lambda: (_ for _ in ()).throw(failure), wait=lambda d, s: self.fail("不应等待"))

    def test_abort_during_backoff(self):
        signal, calls = Signal(), []
        def operation():
            calls.append(1)
            raise RequestFailure("http_error", status=503)
        with self.assertRaises(Aborted):
            run_with_retry(operation, signal=signal, wait=lambda d, s: s.abort())
        self.assertEqual(len(calls), 1)

    def test_abort_discards_late_response(self):
        signal = Signal()
        def operation():
            signal.abort()
            return "late"
        with self.assertRaises(Aborted):
            run_with_retry(operation, signal=signal)

    def test_zero_retries(self):
        with self.assertRaises(RequestFailure):
            run_with_retry(lambda: (_ for _ in ()).throw(RequestFailure("http_error", status=503)),
                           max_retries=0, wait=lambda d, s: self.fail("不应等待"))

    def test_invalid_policy(self):
        for kwargs in ({"max_retries": -1}, {"max_retries": True}, {"max_retries": 11},
                       {"base_delay": float("nan")}, {"max_delay": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_with_retry(lambda: self.fail("不应发送"), **kwargs)


class TransportChecks(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(CONFIG, {"request_retry_base_s": 0, "request_retry_cap_s": 0,
                                         "request_max_retries": 2})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.request = urllib.request.Request("http://127.0.0.1/unused", data=b"{}")

    def test_retry_after_parser(self):
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        self.assertEqual(transport.parse_retry_after("3"), 3)
        self.assertEqual(transport.parse_retry_after("Sun, 11 Oct 2026 00:00:05 GMT", now=now), 5)
        for value in (None, "bad", "nan", "inf"):
            self.assertIsNone(transport.parse_retry_after(value))

    def test_transport_error_is_unknown(self):
        for error in (urllib.error.URLError("private-token"), TimeoutError("private-token")):
            with self.subTest(error=type(error).__name__):
                with patch.object(transport.urllib.request, "urlopen", side_effect=error) as opener:
                    with self.assertRaises(RequestFailure) as raised:
                        transport.post_json(self.request)
                self.assertEqual(raised.exception.category, "outcome_unknown")
                self.assertNotIn("private-token", str(raised.exception))
                self.assertEqual(opener.call_count, 1)

    def test_http_body_not_logged(self):
        error = http_error(401)
        body = error.fp
        with self.assertRaises(RequestFailure) as raised:
            transport.post_json(self.request, opener=lambda *a, **kw: (_ for _ in ()).throw(error))
        self.assertNotIn("private body", str(raised.exception))
        self.assertTrue(body.closed)

    def test_bad_json_not_retried(self):
        with patch.object(transport.urllib.request, "urlopen", return_value=io.BytesIO(b"{bad")) as opener:
            with self.assertRaises(RequestFailure) as raised:
                transport.post_json(self.request)
        self.assertEqual(raised.exception.category, "invalid_response")
        self.assertEqual(opener.call_count, 1)

    def test_truncation_before_tool(self):
        data = llm.fake_tool_response(("write", {"path": "x", "content": "y"}))
        data["choices"][0]["finish_reason"] = "length"
        history = [{"role": "user", "content": "test"}]
        with patch.object(transport.urllib.request, "urlopen", side_effect=lambda *a, **kw: response(data)):
            with patch.object(agent.toolbox, "submit") as submit:
                with self.assertRaises(RequestFailure):
                    agent.agent_loop(history, emit=lambda *a, **kw: None)
        submit.assert_not_called()
        self.assertEqual(len(history), 1)

    def test_invalid_arguments_or_ids(self):
        for duplicate in (False, True):
            data = llm.fake_tool_response(("read", {"path": "x"}))
            calls = data["choices"][0]["message"]["tool_calls"]
            if duplicate:
                calls.append(dict(calls[0]))
            else:
                calls[0]["function"]["arguments"] = "{partial"
            with self.subTest(duplicate=duplicate), self.assertRaises(RequestFailure):
                transport.validate_response(data)

    def test_full_agent_does_not_replay_tool(self):
        tool_reply = llm.fake_tool_response(("write", {"path": "x", "content": "y"}))
        script = [503, tool_reply, 429, llm.fake_text_response("done")]
        bodies, persisted = [], []
        def opener(request, **kwargs):
            bodies.append(request.data)
            item = script.pop(0)
            if isinstance(item, int):
                raise http_error(item)
            return response(item)
        history = [{"role": "user", "content": "test"}]
        with patch.object(transport.urllib.request, "urlopen", side_effect=opener):
            with patch.object(agent.toolbox, "submit", return_value="written") as submit:
                agent.agent_loop(history, emit=lambda *a, **kw: None, on_append=persisted.append)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(len(history), 4)
        self.assertEqual(len(persisted), 3)
        self.assertEqual(len(bodies), 4)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[2], bodies[3])
        self.assertEqual(history[2]["tool_call_id"], history[1]["tool_calls"][0]["id"])

    def test_real_local_http(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(503 if len(requests) == 1 else 200)
                self.end_headers()
                self.wfile.write(json.dumps(llm.fake_text_response("local ok")).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/chat/completions", data=b"{}")
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
            result = transport.post_json(request, opener=direct)
            self.assertEqual(llm.extract_text(result), "local ok")
            self.assertEqual(requests, [b"{}", b"{}"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    setup_console()
    unittest.main(verbosity=2)
