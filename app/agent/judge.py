"""
LLM judge — a lightweight second-pass model that scores whether a tool's
output (or SQL fallback result) actually answered the user's intent.

Verdict shape:
    {
      "verdict": "pass" | "weak" | "fail",
      "reasoning": "<one line>",
      "fix_hint": "<optional>"      # what the orchestrator could try next
    }

- "pass":  the result addresses the question with the right data shape.
- "weak":  the result is tangentially useful but misses something important.
- "fail":  the result is wrong, empty-when-shouldn't-be, or unrelated.

The judge runs on tool outputs AND on SQL fallback results. The orchestrator
can use the verdict to either accept the result, retry with a different tool,
or ask the user to clarify.

Design choice: judge uses the SAME provider as the main loop (cheap models
like llama-3.1-8b or gemini-3.5-flash-lite are fine). We keep the prompt
short so this stays sub-second.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.agent.providers import LLMProvider, get_provider


_JUDGE_SYSTEM = """
You are an impartial judge evaluating whether a tool's output is a good
answer to a user's question. You do NOT answer the question. You just rate
the tool result.

Return STRICT JSON:
  {"verdict": "pass" | "weak" | "fail",
   "reasoning": "<one short sentence>",
   "fix_hint": "<optional short suggestion for what to try instead>"}

Rules:
- "pass": the result's data clearly addresses what the user asked.
- "weak": the result is partially relevant but misses the main thing, OR
  returns far too few rows, OR the data shape doesn't match the question.
- "fail": the result is empty when it clearly shouldn't be, OR is about the
  wrong entity, OR is an error.

Do NOT demand perfection — if the user asked a vague question, "pass" an
on-topic result. Only "fail" when the mismatch is obvious.

Return JSON only. No prose outside the JSON.
""".strip()


@dataclass
class JudgeVerdict:
    verdict: str  # "pass" | "weak" | "fail"
    reasoning: str
    fix_hint: str = ""
    tokens_used: int = 0
    latency_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    @property
    def failed(self) -> bool:
        return self.verdict == "fail"


def judge_tool_result(
    user_question: str,
    tool_name: str,
    tool_args: dict,
    tool_result: object,
    provider: LLMProvider | None = None,
) -> JudgeVerdict:
    """
    Rate whether `tool_result` is a good answer to `user_question`.
    """
    provider = provider or get_provider()
    # Truncate the tool result dump so we don't blow the context on a 200-row
    # SQL fallback.
    result_dump = json.dumps(tool_result, default=str)[:3500]
    args_dump = json.dumps(tool_args, default=str)[:800]

    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"User question: {user_question!r}\n\n"
                f"Tool called: {tool_name}\n"
                f"Args: {args_dump}\n"
                f"Result: {result_dump}\n\n"
                "Return the JSON verdict."
            ),
        },
    ]
    try:
        resp = provider.chat(messages, tools=None, temperature=0.0)
    except Exception as e:
        return JudgeVerdict(
            verdict="pass",  # fail-open on judge errors; don't block the user
            reasoning=f"judge errored: {str(e)[:200]}",
        )

    content = resp.content.strip()
    # Models sometimes wrap JSON in ```json fences
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        data = json.loads(content)
        return JudgeVerdict(
            verdict=data.get("verdict", "pass"),
            reasoning=data.get("reasoning", "")[:300],
            fix_hint=data.get("fix_hint", "")[:300],
            tokens_used=resp.tokens_used,
            latency_ms=resp.latency_ms,
        )
    except json.JSONDecodeError:
        return JudgeVerdict(
            verdict="pass",
            reasoning=f"judge returned non-JSON: {content[:200]}",
            tokens_used=resp.tokens_used,
            latency_ms=resp.latency_ms,
        )


def judge_sql_result(
    user_question: str,
    sql: str,
    intent: str,
    rows: list[dict],
    row_count: int,
    provider: LLMProvider | None = None,
) -> JudgeVerdict:
    """
    Rate whether a SQL fallback result makes sense for the user's question.
    Includes the SQL itself so the judge can spot obvious mismatches
    (user asked X, SQL is counting Y).
    """
    provider = provider or get_provider()
    rows_dump = json.dumps(rows[:10], default=str)[:3000]  # first 10 for speed

    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"User question: {user_question!r}\n\n"
                f"Fallback SQL was used because no scoped tool fit.\n"
                f"Stated intent: {intent!r}\n"
                f"SQL executed: {sql[:800]}\n"
                f"Row count: {row_count}\n"
                f"First rows: {rows_dump}\n\n"
                "Return the JSON verdict. Pay attention to: does the SQL "
                "actually compute what the user asked? Does the result data "
                "match the question?"
            ),
        },
    ]
    try:
        resp = provider.chat(messages, tools=None, temperature=0.0)
    except Exception as e:
        return JudgeVerdict(
            verdict="pass",
            reasoning=f"judge errored: {str(e)[:200]}",
        )

    content = resp.content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        data = json.loads(content)
        return JudgeVerdict(
            verdict=data.get("verdict", "pass"),
            reasoning=data.get("reasoning", "")[:300],
            fix_hint=data.get("fix_hint", "")[:300],
            tokens_used=resp.tokens_used,
            latency_ms=resp.latency_ms,
        )
    except json.JSONDecodeError:
        return JudgeVerdict(
            verdict="pass",
            reasoning=f"judge returned non-JSON: {content[:200]}",
            tokens_used=resp.tokens_used,
            latency_ms=resp.latency_ms,
        )
