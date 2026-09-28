"""LLM-in-loop adapter (OpenAI-compatible chat completions).

Default backend: DeepSeek (reads DEEPSEEK_API_KEY); OPENAI_API_KEY also works.
The autonomous driver enforces call/token budgets through LLMUsage.
The API key is read from the environment only and is never logged.
"""

from __future__ import annotations

import json
import os
import re
import time
import math
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..orchestrator.work_budget import WorkBudgetExceeded


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect would send an unreserved request (and possibly credentials).
        return None


class BudgetExceeded(Exception):
    """LLM call/token budget exhausted; the autonomous loop must stop or degrade."""


class LLMUsage:
    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.estimated_usd: Optional[float] = None
        self.reserved_tokens = 0
        self.unknown_usage_calls = 0

    def to_dict(self) -> Dict[str, Any]:
        cost = None if self.unknown_usage_calls else self.estimated_usd
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_usd": None if cost is None else round(cost, 6),
            "cost_status": "unknown" if cost is None else "estimated",
            "reserved_tokens": self.reserved_tokens,
            "unknown_usage_calls": self.unknown_usage_calls,
        }


class LLMClient:
    """Minimal OpenAI-compatible chat client with retry and budget guards."""

    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None,
                 api_key: Optional[str] = None, max_calls: int = 40,
                 max_tokens_total: int = 300_000, timeout: int = 180,
                 reasoning_effort: Optional[str] = None,
                 json_model: Optional[str] = None,
                 pricing_registry: Optional[Dict[str, Dict[str, float]]] = None):
        self.model = model or os.environ.get("LLM_MODEL") or "deepseek-v4-flash"
        # JSON-output tasks (candidate proposal / audit / novelty verdict) go
        # through the DeepSeek Responses API with natively controlled
        # reasoning budget (reasoning.effort + max_output_tokens), keeping the
        # flash model's full capability. Chat Completions on the reasoning
        # model spends the whole budget on thinking for long structured
        # prompts and returns empty content (verified 2026-08-09).
        self.json_model = (json_model or os.environ.get("LLM_JSON_MODEL")
                           or "deepseek-v4-flash")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL")
                         or "https://api.deepseek.com/").rstrip("/") + "/"
        self.api_key = (api_key or os.environ.get("DEEPSEEK_API_KEY")
                        or os.environ.get("OPENAI_API_KEY") or "")
        if not self.api_key:
            raise RuntimeError("LLM API key missing: set DEEPSEEK_API_KEY or OPENAI_API_KEY")
        self.max_calls = max_calls
        self.max_tokens_total = max_tokens_total
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort or os.environ.get("LLM_REASONING_EFFORT")
        self.usage = LLMUsage()
        self._timeout_provider: Optional[Callable[[], Optional[float]]] = None
        self._work_budget: Optional[Any] = None
        self._budget_lock = threading.RLock()
        self._budget_invalid = False
        self._cost_unknown = False
        # Operator-supplied prices keyed by exact base URL + model. No guessed
        # or hard-coded provider price is presented as an actual invoice.
        self._pricing_registry = pricing_registry or {}
        self._opener = urllib.request.build_opener(_NoRedirect())

    def set_work_budget(self, budget: Optional[Any]) -> None:
        """Attach the caller-owned shared budget; never creates one here."""
        self._work_budget = budget

    def set_timeout_provider(
            self, provider: Optional[Callable[[], Optional[float]]]) -> None:
        """Clamp each request and retry to a caller-owned wall-clock budget."""
        self._timeout_provider = provider

    def _remaining_timeout_budget(self) -> Optional[float]:
        provider = self._timeout_provider
        if provider is None:
            return None
        remaining = provider()
        return None if remaining is None else max(0.0, float(remaining))

    def _request_timeout(self) -> float:
        remaining = self._remaining_timeout_budget()
        if remaining is None:
            return float(self.timeout)
        if remaining < 1.0:
            raise BudgetExceeded("audit round deadline expired before LLM request")
        return min(float(self.timeout), remaining)

    def _retry_delay(self, requested: float) -> float:
        remaining = self._remaining_timeout_budget()
        if remaining is None:
            return requested
        if remaining < 1.0:
            raise BudgetExceeded("audit round deadline expired during LLM retry")
        return min(requested, max(0.0, remaining - 0.25))

    # -- internals ---------------------------------------------------------
    def _reserve_request(self, payload: Dict[str, Any], body: bytes):
        output = payload.get("max_output_tokens", payload.get("max_tokens"))
        if isinstance(output, bool) or not isinstance(output, int) or output < 1:
            raise ValueError("a positive output token limit is required")
        # Conservative byte-based input allowance, including serialized framing.
        # This is a reservation, not a tokenizer measurement. Unexpected provider
        # usage invalidates this client instead of silently extending the budget.
        reserved = len(body) + 1024 + output
        with self._budget_lock:
            self._check_budget(reserved)
            parent = self._work_budget
            if parent is not None:
                try:
                    parent.consume_many({"llm_calls": 1, "llm_tokens": reserved})
                except WorkBudgetExceeded as exc:
                    raise BudgetExceeded(str(exc)) from exc
            self.usage.calls += 1
            self.usage.reserved_tokens += reserved
            self.usage.unknown_usage_calls += 1
            # Return the original parent: changing rounds during an outstanding
            # request must not settle its reservation against the new round.
            return reserved, parent

    def _settle_request(self, data: Any, reservation, model: str) -> None:
        reserved, parent = reservation
        usage = data.get("usage") if isinstance(data, dict) else None
        with self._budget_lock:
            if not isinstance(usage, dict):
                self._cost_unknown = True
                self.usage.estimated_usd = None
                return
            prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
            completion = usage.get("completion_tokens", usage.get("output_tokens"))
            total = usage.get("total_tokens")
            if total is None and all(type(v) is int for v in (prompt, completion)):
                total = prompt + completion
            if (any(type(v) is not int or v < 0 for v in (prompt, completion, total))
                    or total < prompt + completion):
                self._cost_unknown = True
                self.usage.estimated_usd = None
                return  # Unknown billing keeps the whole reservation charged.
            self.usage.reserved_tokens -= reserved
            self.usage.unknown_usage_calls -= 1
            self.usage.prompt_tokens += prompt
            self.usage.completion_tokens += completion
            self.usage.total_tokens += total
            if total > reserved:
                self._budget_invalid = True
                self._cost_unknown = True
                self.usage.estimated_usd = None
                raise BudgetExceeded("provider usage exceeded token reservation")
            if parent is not None:
                parent.release_llm_tokens(reserved - total)
            rates = self._pricing_registry.get(self.base_url + model, {})
            values = [rates.get("input_per_million"), rates.get("output_per_million")]
            if (self._cost_unknown or self.usage.unknown_usage_calls
                    or total != prompt + completion
                    or any(isinstance(v, bool) or not isinstance(v, (int, float))
                           or not math.isfinite(v) or v < 0 for v in values)):
                self._cost_unknown = True
                self.usage.estimated_usd = None
            else:
                self.usage.estimated_usd = (self.usage.estimated_usd or 0.0) + (
                    prompt * values[0] + completion * values[1]) / 1_000_000

    def _post(self, payload: Dict[str, Any], endpoint: str = "chat/completions") -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + endpoint, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key})
        last: Optional[Exception] = None
        for attempt in range(3):
            timeout = self._request_timeout()
            reservation = self._reserve_request(payload, body)
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    response_body = resp.read()
                data = json.loads(response_body.decode("utf-8"))
                self._settle_request(data, reservation, str(payload.get("model", "")))
                remaining = self._remaining_timeout_budget()
                if remaining is not None and remaining < 1.0:
                    raise BudgetExceeded(
                        "audit round deadline expired while awaiting LLM response")
                return data
            except urllib.error.HTTPError as exc:
                last = exc
                exc.close()
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    delay = self._retry_delay(2 * (attempt + 1))
                    if delay:
                        time.sleep(delay)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
                if attempt < 2:
                    delay = self._retry_delay(2 * (attempt + 1))
                    if delay:
                        time.sleep(delay)
                    continue
                raise
        raise last if last is not None else RuntimeError("unreachable")

    def _response_text(self, data: Dict[str, Any]) -> str:
        """Extract concatenated output_text from a Responses API payload."""
        parts = []
        for item in data.get("output") or []:
            for c in item.get("content") or []:
                txt = c.get("text")
                if txt:
                    parts.append(txt)
        return "".join(parts)

    def _responses_call(self, system: str, user: str,
                        max_output_tokens: int = 16000,
                        effort: str = "low") -> str:
        """One DeepSeek Responses API call.

        effort controls thinking mode natively:
        - "low" for structured JSON tasks (S2/S3/S5): natively bounded
          reasoning budget, complete long JSON output (verified 2026-08-09:
          499 reasoning tokens vs Chat Completions 4001 + empty content);
        - "none" for write-code tasks (S4): thinking off. With thinking on,
          the model prepends a task restatement ("We need to write...") to the
          code output and the file fails to compile; effort=none yields clean
          code-only output (verified 2026-08-09: 2/2 simple + 1/1 complex PoC
          compile OK).
        """
        self._check_budget(max_output_tokens)
        payload = {
            "model": self.json_model or "deepseek-v4-flash",
            "input": [{"role": "user", "content": user}],
            "instructions": system,
            "reasoning": {"effort": effort},
            "max_output_tokens": max_output_tokens,
        }
        data = self._post(payload, endpoint="responses")
        return self._response_text(data)

    def _check_budget(self, max_tokens: int) -> None:
        if self._budget_invalid:
            raise BudgetExceeded("provider token accounting invalidated this budget")
        if self.usage.calls >= self.max_calls:
            raise BudgetExceeded("LLM call budget exhausted (max_calls=%d)" % self.max_calls)
        used = self.usage.total_tokens + self.usage.reserved_tokens
        if used + max_tokens > self.max_tokens_total:
            raise BudgetExceeded(
                "LLM token budget exhausted (%d + %d > %d)"
                % (used, max_tokens, self.max_tokens_total))

    # -- public -------------------------------------------------------------
    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 4000,
             temperature: float = 0.2, json_mode: bool = False,
             reasoning_effort: Optional[str] = None,
             model: Optional[str] = None) -> str:
        self._check_budget(max_tokens)
        # Reasoning models occasionally return empty content (all tokens spent
        # on reasoning) or hit 'length'; retry with backoff before giving up.
        for attempt in range(3):
            payload: Dict[str, Any] = {
                "model": model or self.model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            eff = reasoning_effort or self.reasoning_effort
            if eff:
                payload["reasoning_effort"] = eff
            data = self._post(payload)
            choice = (data.get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content") or ""
            if content.strip():
                return content
            if attempt < 2:
                time.sleep(self._retry_delay(1.5))
        return ""

    @staticmethod
    def extract_json(text: str) -> Dict[str, Any]:
        """Robust JSON extraction: whole-string, first {...} block, or code fence."""
        text = text.strip()
        try:
            return json.loads(text)
        except ValueError:
            pass
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                pass
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                pass
        raise ValueError("LLM did not return JSON: %r" % text[:200])

    def ask_json(self, system: str, user: str, max_tokens: int = 4000) -> Dict[str, Any]:
        # JSON-output tasks use DeepSeek Responses API: reasoning budget is
        # natively controlled (reasoning.effort) and max_output_tokens covers
        # both thinking and visible output. Chat Completions on the reasoning
        # model spends the whole budget on thinking for long structured
        # prompts (verified 2026-08-09: content="", reasoning_tokens=4001);
        # Responses API + effort=low yields complete JSON (499 reasoning
        # tokens, 15.9s). Model: deepseek-v4-flash only (Responses API does
        # not support pro yet).
        max_output = max(8000, max_tokens * 2)
        self._check_budget(max_output)
        # high -> low -> none (none disables thinking; last-resort fallback for
        # long JSON prompts where thinking models keep restating the task).
        efforts = [self.reasoning_effort or "high", "low", "none"]
        last_exc: Optional[Exception] = None
        for effort in efforts:
            try:
                text = self._responses_call(system, user,
                                            max_output_tokens=max_output,
                                            effort=effort)
                if text.strip():
                    return self.extract_json(text)
            except ValueError as exc:
                last_exc = exc
                continue
            except BudgetExceeded:
                raise
            except Exception:
                continue
        # Fallback: plain mode with an explicit "JSON only" instruction.
        try:
            return self.extract_json(self.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user
                  + "\n\n只输出 JSON 对象本身，不要 Markdown 围栏，不要任何其他文字。"}],
                max_tokens=max_tokens, json_mode=False, model=self.json_model))
        except ValueError:
            raise last_exc or ValueError("LLM did not return JSON")

    def ask_responses(self, system: str, user: str,
                      max_output_tokens: int = 16000) -> str:
        """Long-form generation (PoC writing) via Responses API: keeps the
        flash model's capability while avoiding Chat Completions' long-task
        timeout/empty-content failure. Thinking is off (effort=none) so the
        output is code-only; verified 2026-08-09: simple and complex PoCs
        compile OK."""
        return self._responses_call(system, user,
                                    max_output_tokens=max_output_tokens,
                                    effort="none")

    def ask(self, system: str, user: str, max_tokens: int = 4000,
            temperature: float = 0.2, json_mode: bool = False,
            reasoning_effort: Optional[str] = None) -> str:
        # Unified Responses API path for write-code / long-text generation.
        # effort defaults to high (user preference; matches Codex daily use);
        # reasoning models may prepend a task restatement before the code, so
        # callers must extract the code segment (run_agent.extract_java_code).
        # Verified 2026-08-09: high + extraction = 3/3 compile OK.
        return self._responses_call(
            system, user,
            max_output_tokens=max(16000, max_tokens * 2),
            effort=(reasoning_effort or self.reasoning_effort or "high"))
