"""Response-contract checks for RustTestAgent LLM replies.

This module keeps output-shape validation separate from repair logic. A
contract violation is not treated as a functional repair failure; it produces
focused feedback for the next LLM round and lets the normal repair loop
continue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ResponseContractViolation:
    code: str
    log_message: str
    history_feedback: str


class RepairResponseContract:
    SUMMARY_LIMIT = 800
    UPDATED_SUMMARY_LIMIT = 500
    RAW_TAIL_CHARS = 800

    @classmethod
    def validate_payload(cls, payload: Dict[str, Any]) -> Optional[ResponseContractViolation]:
        oversized = cls._oversized_text_fields(payload)
        if not oversized:
            return None
        return ResponseContractViolation(
            code="oversized_text_fields",
            log_message=f"LLM JSON 字段超长：{', '.join(oversized)}",
            history_feedback=(
                "[System] The previous reply was valid JSON but violated the response contract: "
                f"{', '.join(oversized)}. Do not expand the analysis in visible content. "
                f"Return compact JSON only: summary <= {cls.SUMMARY_LIMIT} chars and "
                f"updated_summary <= {cls.UPDATED_SUMMARY_LIMIT} chars. If more evidence is needed, "
                "use the read-request fields and leave edits empty."
            ),
        )

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
                "Return exactly one compact raw JSON object next. No markdown fences, no text outside JSON, "
                f"summary <= {cls.SUMMARY_LIMIT} chars, updated_summary <= {cls.UPDATED_SUMMARY_LIMIT} chars."
            ),
        )

    @classmethod
    def _oversized_text_fields(cls, payload: Dict[str, Any]) -> List[str]:
        limits = {
            "summary": cls.SUMMARY_LIMIT,
            "updated_summary": cls.UPDATED_SUMMARY_LIMIT,
        }
        oversized: List[str] = []
        for key, limit in limits.items():
            value = payload.get(key)
            if value is None:
                continue
            if len(str(value)) > limit:
                oversized.append(f"{key}>{limit}")
        return oversized
