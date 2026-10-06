"""
LLM provider abstraction — Groq (default) + Gemini (fallback), swappable.

Both support tool-calling via a normalized LLMResponse dataclass. The
orchestrator depends only on this interface, not on any specific SDK.

Env vars:
    GROQ_API_KEY
    GEMINI_API_KEY
    LLM_PROVIDER      (default "groq"; "gemini" also supported)
    LLM_MODEL         (default "llama-3.3-70b-versatile" for groq,
                       "gemini-2.0-flash-exp" for gemini)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx


# ----------------------------------------------------------------------------
# Normalized shapes
# ----------------------------------------------------------------------------
@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    tokens_used: int = 0
    latency_ms: int = 0

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


# ----------------------------------------------------------------------------
# Base
# ----------------------------------------------------------------------------
class LLMProvider:
    name: str = "base"
    default_model: str = ""

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        stream_callback: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """
        If `stream_callback` is provided, the provider SHOULD stream text
        chunks as they arrive, calling the callback with each delta.
        Tool-call deltas are accumulated internally and returned on the
        final `LLMResponse` just like the non-streaming path.
        Providers that don't implement streaming can ignore the callback.
        """
        raise NotImplementedError


# ----------------------------------------------------------------------------
# Groq (OpenAI-compatible)
# ----------------------------------------------------------------------------
class GroqProvider(LLMProvider):
    name = "groq"
    # Overridable via LLM_MODEL env var.
    default_model = os.environ.get("LLM_MODEL") or "llama-3.1-8b-instant"
    base_url = "https://api.groq.com/openai/v1"

    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        stream_callback: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream_callback is not None:
            payload["stream"] = True

        if stream_callback is not None:
            return self._chat_streamed(payload, stream_callback)
        return self._chat_sync(payload)

    # ------------------------------------------------------------------
    def _chat_sync(self, payload: dict) -> LLMResponse:
        t0 = time.time()
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code >= 400:
                try:
                    err = resp.json()
                except Exception:
                    err = {"raw": resp.text[:500]}
                raise RuntimeError(
                    f"Groq API {resp.status_code} on model "
                    f"{payload['model']!r}: {err}"
                )
            data = resp.json()
        latency_ms = int((time.time() - t0) * 1000)

        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args))

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=data.get("model", payload["model"]),
            tokens_used=usage.get("total_tokens", 0),
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    def _chat_streamed(
        self, payload: dict, cb: Callable[[str], None],
    ) -> LLMResponse:
        """
        Server-Sent-Events streaming over Groq's OpenAI-compatible endpoint.
        Content deltas get forwarded to `cb` as they arrive. Tool-call deltas
        are accumulated by `index` (names arrive once up-front, args stream
        character-by-character) and reconstructed into ToolCall objects when
        the stream ends.
        """
        t0 = time.time()
        content_bits: list[str] = []
        tool_builders: dict[int, dict] = {}
        model_used = payload["model"]

        with httpx.Client(timeout=90.0) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"Groq API {resp.status_code} on model "
                        f"{payload['model']!r}: {body[:500]}"
                    )
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    # Content streaming
                    piece = delta.get("content")
                    if piece:
                        content_bits.append(piece)
                        try:
                            cb(piece)
                        except Exception:
                            # Never let a UI callback crash the stream
                            pass

                    # Tool-call streaming (delta.tool_calls is a list of
                    # partial updates keyed by `index`)
                    for tc in (delta.get("tool_calls") or []):
                        idx = tc.get("index", 0)
                        b = tool_builders.setdefault(
                            idx, {"name": "", "arguments": ""}
                        )
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            b["name"] = fn["name"]
                        if fn.get("arguments"):
                            b["arguments"] += fn["arguments"]

                    # Model name is in the chunk envelope
                    if chunk.get("model"):
                        model_used = chunk["model"]

        latency_ms = int((time.time() - t0) * 1000)
        content = "".join(content_bits)

        tool_calls: list[ToolCall] = []
        for b in tool_builders.values():
            try:
                args = json.loads(b["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if b["name"]:
                tool_calls.append(ToolCall(name=b["name"], arguments=args))

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=model_used,
            tokens_used=0,  # streaming endpoint doesn't send usage
            latency_ms=latency_ms,
        )


# ----------------------------------------------------------------------------
# Gemini (Google generative-language REST API)
# ----------------------------------------------------------------------------
class GeminiProvider(LLMProvider):
    name = "gemini"
    # Overridable via LLM_MODEL env var.
    default_model = os.environ.get("LLM_MODEL") or "gemini-3.5-flash-lite"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

    @staticmethod
    def _to_gemini_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        """
        Convert OpenAI-style messages to Gemini format.
        Returns (system_instruction, contents).
        """
        system_bits: list[str] = []
        contents: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system_bits.append(str(m.get("content", "")))
                continue
            if role == "tool":
                # tool results in gemini go as a "function" role part
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.get("name", "tool"),
                                    "response": {"result": str(m.get("content", ""))},
                                }
                            }
                        ],
                    }
                )
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append(
                {"role": gemini_role, "parts": [{"text": str(m.get("content", ""))}]}
            )
        return "\n\n".join(system_bits), contents

    @staticmethod
    def _to_gemini_tools(tools: list[dict]) -> list[dict]:
        """Convert OpenAI tool defs to Gemini functionDeclarations."""
        declarations = []
        for t in tools:
            fn = t.get("function", t)
            declarations.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return [{"functionDeclarations": declarations}]

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        stream_callback: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        # Streaming not implemented for Gemini yet — fall through to non-stream
        # (interface stays uniform so the orchestrator doesn't branch).
        model = model or self.default_model
        system_instruction, contents = self._to_gemini_messages(messages)

        payload: dict = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            payload["tools"] = self._to_gemini_tools(tools)

        t0 = time.time()
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.base_url}/models/{model}:generateContent?key={self.api_key}",
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code >= 400:
                try:
                    err = resp.json()
                except Exception:
                    err = {"raw": resp.text[:500]}
                raise RuntimeError(
                    f"Gemini API {resp.status_code} on model {model!r}: {err}"
                )
            data = resp.json()
        latency_ms = int((time.time() - t0) * 1000)

        parts = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        content_bits = []
        tool_calls: list[ToolCall] = []
        for p in parts:
            if "text" in p:
                content_bits.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append(
                    ToolCall(name=fc.get("name", ""), arguments=fc.get("args", {}))
                )

        usage = data.get("usageMetadata", {})
        return LLMResponse(
            content="".join(content_bits),
            tool_calls=tool_calls,
            model=model,
            tokens_used=usage.get("totalTokenCount", 0),
            latency_ms=latency_ms,
        )


# ----------------------------------------------------------------------------
# FallbackProvider — tries a chain of (provider, model) pairs in order and
# returns on the first success. Lets us ride through transient 429s, model
# overloads, or provider outages without ever showing the user an error.
# ----------------------------------------------------------------------------
class FallbackProvider(LLMProvider):
    name = "fallback"

    def __init__(self, chain: list[tuple[LLMProvider, str | None]]):
        """
        chain: ordered list of (provider_instance, model_override). The first
        entry is tried first; on exception, the next one is tried, etc. A
        model_override of None means "use that provider's default model."
        """
        if not chain:
            raise RuntimeError("FallbackProvider needs at least one provider")
        self.chain = chain
        # For logging / telemetry
        self.default_model = "+".join(
            f"{p.name}:{m or p.default_model}" for p, m in chain
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.3,
        stream_callback: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        failures: list[str] = []
        for i, (provider, mdl) in enumerate(self.chain):
            use_model = model or mdl  # explicit model arg still wins
            try:
                resp = provider.chat(
                    messages, tools=tools,
                    model=use_model, temperature=temperature,
                    stream_callback=stream_callback,
                )
                if i > 0:
                    # We successfully fell back — surface it in telemetry
                    print(
                        f"[fallback] succeeded on tier {i} "
                        f"({provider.name}:{use_model or provider.default_model}) "
                        f"after {len(failures)} failure(s)"
                    )
                return resp
            except Exception as e:
                tier = f"{provider.name}:{use_model or provider.default_model}"
                failures.append(f"{tier} → {str(e)[:200]}")
                # Streaming already emitted some content for this tier — if
                # we fall back we discard it, which is correct behavior.
                continue
        raise RuntimeError(
            f"All {len(self.chain)} LLM tiers failed:\n  " + "\n  ".join(failures)
        )


# ----------------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------------
_PROVIDER_BUILDERS = {
    "groq":   GroqProvider,
    "gemini": GeminiProvider,
}


def _build_single(name: str) -> LLMProvider:
    name = name.lower()
    builder = _PROVIDER_BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"Unknown LLM provider: {name!r}")
    return builder()


def _build_fallback(chain_str: str) -> FallbackProvider:
    """
    Parse a chain spec like:
      "groq:llama-3.1-8b-instant,groq:llama-3.3-70b-versatile,gemini:gemini-3.5-flash-lite"
    into a FallbackProvider. Entries with no model use the provider default.
    Entries whose provider can't be built (e.g. missing API key) are
    silently skipped — this is intentional so a user with only one key
    still gets a working provider.
    """
    pairs: list[tuple[LLMProvider, str | None]] = []
    for raw in chain_str.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            pname, model = raw.split(":", 1)
            model = model.strip() or None
        else:
            pname, model = raw, None
        try:
            provider = _build_single(pname)
        except Exception as e:
            print(f"[fallback] tier {raw!r} skipped: {e}")
            continue
        pairs.append((provider, model))
    if not pairs:
        raise RuntimeError(
            f"Fallback chain {chain_str!r} has no usable tiers "
            "(check API keys and provider names)"
        )
    return FallbackProvider(pairs)


def get_provider(name: str | None = None) -> LLMProvider:
    """
    Resolve the active LLM provider from `name` or the LLM_PROVIDER env var.

    Values:
      - "groq"      → single Groq provider, uses LLM_MODEL or default
      - "gemini"    → single Gemini provider, uses LLM_MODEL or default
      - "fallback"  → multi-tier fallback (default). Chain comes from
                      LLM_FALLBACK_CHAIN. Default chain:
                        "groq:,groq:llama-3.3-70b-versatile,gemini:"
                      Blank model slots use that provider's default.
    """
    name = (name or os.environ.get("LLM_PROVIDER") or "fallback").lower()
    if name == "fallback":
        chain_str = os.environ.get(
            "LLM_FALLBACK_CHAIN",
            "groq:,groq:llama-3.3-70b-versatile,gemini:",
        )
        return _build_fallback(chain_str)
    return _build_single(name)
