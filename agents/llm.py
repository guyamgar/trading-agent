"""
עטיפת subprocess סביב Claude CLI.
מנצלת את ה-OAuth של המשתמש בClaude Code - אין צורך ב-API key.
"""
import json
import subprocess
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class LLMResponse:
    raw_result: str
    parsed: Optional[Dict[str, Any]]
    cost_usd: float
    duration_ms: int
    is_error: bool
    error_message: Optional[str] = None


def call_claude(
    user_prompt: str,
    system_prompt: str,
    model: str = "sonnet",
    timeout: int = 240,
) -> LLMResponse:
    """
    קורא ל-Claude CLI במצב לא אינטראקטיבי.
    מחזיר תגובה מפורסרת כ-JSON אם הפלט תקין.

    model: 'sonnet' / 'haiku' / 'opus' / שם מלא של מודל
    """
    cmd = [
        "claude",
        "-p",
        "--tools", "",
        "--model", model,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--exclude-dynamic-system-prompt-sections",
        user_prompt,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return LLMResponse(
            raw_result="",
            parsed=None,
            cost_usd=0,
            duration_ms=timeout * 1000,
            is_error=True,
            error_message=f"timeout after {timeout}s",
        )

    if proc.returncode != 0:
        return LLMResponse(
            raw_result=proc.stderr,
            parsed=None,
            cost_usd=0,
            duration_ms=0,
            is_error=True,
            error_message=f"claude CLI exit {proc.returncode}: {proc.stderr[:200]}",
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return LLMResponse(
            raw_result=proc.stdout,
            parsed=None,
            cost_usd=0,
            duration_ms=0,
            is_error=True,
            error_message=f"לא הצלחתי לפרסר את תגובת הCLI: {e}",
        )

    if envelope.get("is_error"):
        return LLMResponse(
            raw_result=str(envelope.get("result", "")),
            parsed=None,
            cost_usd=float(envelope.get("total_cost_usd", 0)),
            duration_ms=int(envelope.get("duration_ms", 0)),
            is_error=True,
            error_message=str(envelope.get("result", "unknown error")),
        )

    raw_result = envelope.get("result", "")
    parsed = _extract_json(raw_result)
    cost = float(envelope.get("total_cost_usd", 0))

    # תיעוד עלות לסטטיסטיקה יומית
    if cost > 0:
        try:
            from memory_store import log_llm_cost
            log_llm_cost(cost)
        except Exception:
            pass

    return LLMResponse(
        raw_result=raw_result,
        parsed=parsed,
        cost_usd=cost,
        duration_ms=int(envelope.get("duration_ms", 0)),
        is_error=False,
    )


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    שולף JSON מפלט - גם אם עטוף ב-```json ... ```
    """
    text = text.strip()

    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    if not text.startswith("{") and "{" in text:
        text = text[text.index("{"):]
        last_close = text.rfind("}")
        if last_close > 0:
            text = text[:last_close + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
