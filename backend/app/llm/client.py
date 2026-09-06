"""OpenAI-compatible LLM client (D1): JSON mode, tool loop, retries, fake provider.

Prompts live in app/prompts/*.md and are loaded via load_prompt() — never inline.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.utils.logging import json_log

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_PROMPT_CACHE: dict[str, str] = {}

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class LLMError(Exception):
    pass


def load_prompt(name: str, **vars: str) -> str:
    if name not in _PROMPT_CACHE:
        _PROMPT_CACHE[name] = (PROMPTS_DIR / f"{name}.md").read_text()
    text = _PROMPT_CACHE[name]
    for key, val in vars.items():
        text = text.replace("{{" + key + "}}", val)
    return text


def prompt_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


@dataclass
class LLMUsage:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def cost_usd(self) -> float:
        return round(
            self.tokens_in * settings.llm_cost_in_per_mtok / 1e6
            + self.tokens_out * settings.llm_cost_out_per_mtok / 1e6,
            6,
        )


@dataclass
class LLMResult:
    output: BaseModel
    usage: LLMUsage
    tool_trace: list[dict] = field(default_factory=list)


def _extract_json(content: str) -> dict:
    text = content.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


class BaseLLMClient:
    async def json_call(
        self, system: str, user: str, schema: type[BaseModel]
    ) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError

    async def run_tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        executor: ToolExecutor,
        schema: type[BaseModel],
        max_rounds: int = 8,
    ) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError

    async def ping(self) -> bool:
        return True


class FakeLLMClient(BaseLLMClient):
    """Deterministic canned LLM for CI/offline dev/tests (§4 LLM_PROVIDER=fake).

    Script steps:
      {"tool_calls": [{"name": "...", "args": {...}}, ...]}   -> executor invoked
      {"json": {...}}                                         -> validated final answer
    """

    def __init__(self, script: list[dict] | None = None) -> None:
        self.script = script or []
        self._i = 0
        self.executed_tools: list[dict] = []

    def _next(self) -> dict:
        if self._i >= len(self.script):
            raise LLMError("FakeLLM: script exhausted")
        step = self.script[self._i]
        self._i += 1
        return step

    async def json_call(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        step = self._next()
        usage = LLMUsage(calls=1, tokens_in=len(system) + len(user) // 4, tokens_out=64)
        return LLMResult(output=schema.model_validate(step["json"]), usage=usage)

    async def run_tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        executor: ToolExecutor,
        schema: type[BaseModel],
        max_rounds: int = 8,
    ) -> LLMResult:
        usage = LLMUsage()
        trace: list[dict] = []
        while True:
            step = self._next()
            if "json" in step:
                usage.calls += 1
                return LLMResult(
                    output=schema.model_validate(step["json"]), usage=usage, tool_trace=trace
                )
            for tc in step.get("tool_calls", []):
                result = await executor(tc["name"], tc.get("args", {}))
                self.executed_tools.append({"name": tc["name"], "args": tc.get("args", {})})
                trace.append({"tool": tc["name"], "result": result})
                usage.calls += 1


class OpenAICompatibleClient(BaseLLMClient):
    """Thin client over any OpenAI-compatible endpoint (OpenAI, GLM, Groq, vLLM)."""

    def __init__(self) -> None:
        from openai import AsyncOpenAI  # lazy: fake path stays dependency-free

        self.client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_s,
            max_retries=0,  # retries handled here
        )
        self.model = settings.llm_model

    async def _chat(self, messages: list[dict], tools: list[dict] | None, usage: LLMUsage):
        import openai
        from tenacity import (
            retry,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )

        @retry(
            retry=retry_if_exception_type(
                (
                    openai.RateLimitError,
                    openai.APIConnectionError,
                    openai.APITimeoutError,
                    openai.InternalServerError,
                )
            ),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            stop=stop_after_attempt(3),
            reraise=True,
        )
        async def _call():
            return await self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                **({"tools": tools, "tool_choice": "auto"} if tools else {}),
            )

        resp = await _call()
        if resp.usage:
            usage.tokens_in += resp.usage.prompt_tokens
            usage.tokens_out += resp.usage.completion_tokens
        usage.calls += 1
        return resp

    async def json_call(self, system: str, user: str, schema: type[BaseModel]) -> LLMResult:
        usage = LLMUsage()
        schema_hint = json.dumps(schema.model_json_schema())
        ask = f"Respond ONLY with JSON matching: {schema_hint}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{user}\n\n{ask}"},
        ]
        last_err = ""
        for _attempt in range(3):
            resp = await self._chat(messages, None, usage)
            content = resp.choices[0].message.content or ""
            try:
                return LLMResult(output=schema.model_validate(_extract_json(content)), usage=usage)
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = str(e)[:500]
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": f"Invalid JSON: {last_err}. {ask}"})
        raise LLMError(f"invalid JSON after retries: {last_err}")

    async def run_tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        executor: ToolExecutor,
        schema: type[BaseModel],
        max_rounds: int = 8,
    ) -> LLMResult:
        usage = LLMUsage()
        trace: list[dict] = []
        schema_hint = json.dumps(schema.model_json_schema())
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        corrections = 0
        for _round in range(max_rounds):
            resp = await self._chat(messages, tools, usage)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [tc.model_dump() for tc in tool_calls],
                    }
                )
                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    try:
                        result = await executor(name, args)
                    except Exception as e:  # tools never raise to the LLM (§9)
                        result = {"error": str(e)[:300], "reason": "tool_failed"}
                    trace.append({"tool": name, "args": args, "result": result})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    })
                continue
            content = msg.content or ""
            try:
                parsed = schema.model_validate(_extract_json(content))
                return LLMResult(output=parsed, usage=usage, tool_trace=trace)
            except (json.JSONDecodeError, ValidationError) as e:
                corrections += 1
                if corrections > 2:
                    raise LLMError(f"invalid JSON in tool loop: {str(e)[:300]}") from e
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {"role": "user", "content": f"Invalid JSON: {str(e)[:300]}. {schema_hint}"}
                )
        raise LLMError("tool loop exceeded max rounds")

    async def ping(self) -> bool:
        import httpx

        try:
            r = await httpx.AsyncClient(timeout=10).get(
                f"{settings.llm_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False


def get_llm_client() -> BaseLLMClient:
    if settings.llm_provider == "fake":
        return FakeLLMClient()
    return OpenAICompatibleClient()


__all__ = [
    "BaseLLMClient",
    "FakeLLMClient",
    "LLMError",
    "LLMResult",
    "LLMUsage",
    "OpenAICompatibleClient",
    "get_llm_client",
    "json_log",
    "load_prompt",
    "prompt_hash",
]
