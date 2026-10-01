"""Actual transport attempts, including retries, must own reservations."""

import io
import json
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from agent.llm.adapter import BudgetExceeded, LLMClient
from agent.orchestrator.work_budget import WorkBudget, WorkBudgetExceeded
from agent.autonomous.run_multi import _render_md


def response(content="ok", usage=None, **extra):
    data = {"choices": [{"message": {"content": content}}], **extra}
    if usage is not None:
        data["usage"] = usage
    return io.BytesIO(json.dumps(data).encode())


USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
MESSAGES = [{"role": "user", "content": "hello"}]


class LLMBudgetTests(unittest.TestCase):
    def client(self, **kwargs):
        return LLMClient(api_key="test-only", **kwargs)

    def test_empty_response_cannot_exceed_call_limit(self):
        client = self.client(max_calls=1)
        with patch.object(client._opener, "open", return_value=response("", USAGE)) as send:
            with patch("agent.llm.adapter.time.sleep"):
                with self.assertRaises(BudgetExceeded):
                    client.chat(MESSAGES, max_tokens=10)
        self.assertEqual(1, send.call_count)
        self.assertEqual(1, client.usage.calls)
        self.assertEqual(15, client.usage.total_tokens)

    def test_http_and_timeout_retries_each_consume_call(self):
        for error in (urllib.error.HTTPError("test", 429, "rate", {}, None),
                      urllib.error.HTTPError("test", 503, "busy", {}, None),
                      urllib.error.URLError("unknown billing"), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                client = self.client(max_calls=1)
                with patch.object(client._opener, "open", side_effect=error) as send:
                    with patch("agent.llm.adapter.time.sleep"):
                        with self.assertRaises(BudgetExceeded):
                            client.chat(MESSAGES, max_tokens=10)
                self.assertEqual(1, send.call_count)
                self.assertGreater(client.usage.reserved_tokens, 10)
                self.assertEqual("unknown", client.usage.to_dict()["cost_status"])

    def test_input_tokens_reserved_before_send(self):
        client = self.client(max_tokens_total=1100)
        with patch.object(client._opener, "open") as send:
            with self.assertRaises(BudgetExceeded):
                client.chat(MESSAGES, max_tokens=10)
        send.assert_not_called()
        self.assertEqual(0, client.usage.calls)

    def test_unknown_or_invalid_usage_never_refunds(self):
        for usage in (None, {}, {"total_tokens": 1},
                      {"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0},
                      {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 1}):
            client = self.client(max_tokens_total=2000)
            with patch.object(client._opener, "open", return_value=response(usage=usage)) as send:
                self.assertEqual("ok", client.chat(MESSAGES, max_tokens=10))
                with self.assertRaises(BudgetExceeded):
                    client.chat(MESSAGES, max_tokens=10)
            self.assertEqual(1, send.call_count)
            self.assertEqual(1, client.usage.unknown_usage_calls)

    def test_responses_settles_parent_and_counts_reasoning_in_output(self):
        client = self.client()
        root = WorkBudget(llm_calls=1, llm_tokens=3000)
        client.set_work_budget(root)
        usage = {"input_tokens": 10, "output_tokens": 20,
                 "output_tokens_details": {"reasoning_tokens": 15}}
        with patch.object(client._opener, "open", return_value=response(
                usage=usage, output=[{"content": [{"text": "done"}]}])):
            self.assertEqual("done", client.ask_responses("s", "u", 100))
        self.assertEqual(30, client.usage.total_tokens)
        self.assertEqual(2970, root.remaining("llm_tokens"))
        self.assertEqual(0, root.remaining("llm_calls"))

    def test_usage_over_reservation_stops_future_requests(self):
        client = self.client()
        excessive = {"prompt_tokens": 1, "completion_tokens": 10000, "total_tokens": 10001}
        with patch.object(client._opener, "open", return_value=response(usage=excessive)) as send:
            for _ in range(2):
                with self.assertRaises(BudgetExceeded):
                    client.chat(MESSAGES, max_tokens=10)
        self.assertEqual(1, send.call_count)

    def test_concurrent_requests_cannot_overbook_client(self):
        client = self.client(max_calls=1)
        barrier = threading.Barrier(8)
        def call():
            barrier.wait(timeout=10)
            try:
                return client.chat(MESSAGES, max_tokens=10)
            except BudgetExceeded:
                return "blocked"
        with patch.object(client._opener, "open", side_effect=lambda *a, **k: response(usage=USAGE)) as send:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: call(), range(8)))
        self.assertEqual(1, results.count("ok"))
        self.assertEqual(1, send.call_count)

    def test_shared_parent_reservation_is_atomic_across_clients(self):
        root = WorkBudget(llm_calls=1, llm_tokens=3000)
        barrier = threading.Barrier(8)
        def call(_):
            client = self.client()
            client.set_work_budget(root.child("worker"))
            barrier.wait(timeout=10)
            with patch.object(client._opener, "open", return_value=response(usage=USAGE)):
                try:
                    return client.chat(MESSAGES, max_tokens=10)
                except BudgetExceeded:
                    return "blocked"
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(8)))
        self.assertEqual(1, results.count("ok"))
        self.assertEqual(15, root.snapshot()["used"]["llm_tokens"])

    def test_failed_multi_resource_reservation_charges_nothing(self):
        root = WorkBudget(llm_calls=2, llm_tokens=5)
        with self.assertRaises(WorkBudgetExceeded):
            root.child("worker").consume_many({"llm_calls": 1, "llm_tokens": 6})
        self.assertEqual(2, root.remaining("llm_calls"))
        self.assertEqual(5, root.remaining("llm_tokens"))

    def test_deadline_prevents_send_and_empty_response_delay_is_clamped(self):
        client = self.client()
        now = [0.0]
        client.set_timeout_provider(lambda: 1.1 - now[0])
        def sleep(delay):
            self.assertLessEqual(delay, 0.85 + 1e-9)
            now[0] += delay
        with patch.object(client._opener, "open", return_value=response("", USAGE)) as send:
            with patch("agent.llm.adapter.time.sleep", side_effect=sleep):
                with self.assertRaises(BudgetExceeded):
                    client.chat(MESSAGES, max_tokens=10)
        self.assertEqual(1, send.call_count)

    def test_price_requires_explicit_provider_model_rate(self):
        for registry, expected in ((None, None), ({"https://api.deepseek.com/model": {
                "input_per_million": 2, "output_per_million": 4}}, 0.00004)):
            client = self.client(model="model", pricing_registry=registry)
            with patch.object(client._opener, "open", return_value=response(usage=USAGE)):
                client.chat(MESSAGES, max_tokens=10)
            self.assertEqual(expected, client.usage.to_dict()["estimated_usd"])

    def test_unknown_cost_renders_without_fabricated_invoice(self):
        markdown = _render_md([dict(target="fixture", rounds=1, candidates=0,
                                   confirmed=0, excluded=0, llm_calls=1, tokens=15,
                                   estimated_usd=None, elapsed_s=1)])
        self.assertIn("unknown", markdown)
        self.assertNotIn("真实$", markdown)


if __name__ == "__main__":
    unittest.main()
