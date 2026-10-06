"""
Safety / policy guards — auth gate, prompt-injection detection, rate limiting.

These sit between the LLM's requested action and the actual tool execution.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from app.agent.auth import AuthContext
from app.agent.tools import ToolScope, get_tool


# ----------------------------------------------------------------------------
# Auth gate
# ----------------------------------------------------------------------------
def authorize_tool_call(
    ctx: AuthContext, tool_name: str, tool_args: dict
) -> tuple[bool, str]:
    """
    Return (ok, reason). ok=True allows the call to proceed; ok=False means
    refuse and tell the caller why.
    """
    spec = get_tool(tool_name)
    if spec is None:
        return False, f"Unknown tool: {tool_name}"

    scope = spec.scope

    if scope == ToolScope.OTHER_USER:
        return False, "I'm not able to share information about other individual users."

    # SELF_DATA tools can't accept a user_id override — orchestrator binds ctx
    if scope == ToolScope.SELF_DATA:
        # If the LLM tried to pass user_id in args, strip it (we always use ctx)
        if "user_id" in tool_args:
            tool_args.pop("user_id", None)
        return True, ""

    # CATALOG / AGGREGATE are open
    return True, ""


# ----------------------------------------------------------------------------
# Prompt-injection sniffer (lightweight — a layered defense, not a cure)
# ----------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)", re.I),
    re.compile(r"disregard\s+(the\s+)?(system|prompt|rules|guidelines)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+\w+", re.I),
    re.compile(r"reveal\s+(the\s+)?(system|prompt|instructions)", re.I),
    re.compile(r"tell\s+me\s+(about|everything\s+about)\s+user[\s_-]*\d", re.I),
    re.compile(r"all\s+users['\s]+personal", re.I),
    re.compile(r"show\s+me\s+other\s+users", re.I),
]


def detect_prompt_injection(user_input: str) -> tuple[bool, str]:
    """Return (is_injection, matched_pattern)."""
    for pat in _INJECTION_PATTERNS:
        m = pat.search(user_input)
        if m:
            return True, m.group(0)
    return False, ""


# ----------------------------------------------------------------------------
# Rate limiter — in-process, per-user sliding window.
# ----------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, max_requests: int = 30, window_seconds: int = 3600):
        self.max = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, user_id: str) -> tuple[bool, int]:
        """Return (allowed, remaining)."""
        now = time.time()
        q = self._hits[user_id]
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.max:
            return False, 0
        q.append(now)
        return True, self.max - len(q)


_GLOBAL_LIMITER = RateLimiter()


def check_rate_limit(user_id) -> tuple[bool, int]:
    return _GLOBAL_LIMITER.check(str(user_id))
