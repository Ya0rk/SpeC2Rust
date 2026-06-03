"""Response-contract checks for RustTestAgent LLM replies.

This module keeps output-shape validation separate from repair logic. A
contract violation is not treated as a functional repair failure; it produces
focused feedback for the next LLM round and lets the normal repair loop
continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ResponseContractViolation:
    code: str
    log_message: str
    history_feedback: str


class RepairResponseContract:
    RAW_TAIL_CHARS = 800

    @classmethod
    def validate_payload(cls, payload: Dict[str, Any]) -> Optional[ResponseContractViolation]:
        return None

    @classmethod
    def parse_failure(
        cls,
        *,
        text: str,
        usage: Any,
        attempt: int,
        consecutive_count: int,
    ) -> ResponseContractViolation:
        finish_reason = ""
        stream_note = ""
        if isinstance(usage, dict):
            finish_reason = str(usage.get("finish_reason") or "")
            diagnostics = usage.get("stream_diagnostics")
            if isinstance(diagnostics, dict):
                stream_note = (
                    f" stream_diagnostics: content_chars={diagnostics.get('content_chars')}, "
                    f"reasoning_chars={diagnostics.get('reasoning_chars')}, "
                    f"visible_content_empty={diagnostics.get('visible_content_empty')}."
                )

        if finish_reason == "length":
            detail = (
                "The previous response hit the completion token limit before a complete JSON object was produced. "
                "This was visible `content`, not useful hidden reasoning. Do not continue that text."
            )
        else:
            raw_tail = (text or "")[-cls.RAW_TAIL_CHARS :].strip()
            detail = f"Tail of the previous raw reply:\n```\n{raw_tail}\n```"

        return ResponseContractViolation(
            code="unparseable_json",
            log_message=(
                "LLM 返回不可解析为 JSON"
                f"（连续协议失败 {consecutive_count} 次，finish_reason={finish_reason or 'unknown'}）"
            ),
            history_feedback=(
                f"[System] The LLM output from round {attempt} could not be parsed as JSON. "
                f"This is a response-contract violation, not a reason to stop the repair flow. "
                f"finish_reason={finish_reason or 'unknown'}.{stream_note}\n"
                f"{detail}\n"
                "Return exactly one raw JSON object next. No markdown fences and no text outside JSON."
            ),
        )
