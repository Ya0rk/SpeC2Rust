import json
import os
import re
import threading
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


_DEFAULT_RUN_NAME = datetime.now().strftime("%Y%m%d-%H%M%S")
_GLOBAL_COUNTER = count(1)
_GLOBAL_LOCK = threading.Lock()


class RoundLogger:
    """
    Writes one LLM request/reply pair per human-readable Markdown file.

    The logger is deliberately independent from agent classes so the low-level
    Model wrapper can log every round without coupling to individual workflows.
    """

    def __init__(
        self,
        base_dir: Optional[Path | str] = None,
        run_name: Optional[str] = None,
        project_name: Optional[str] = None,
    ):
        repo_root = Path(__file__).resolve().parents[2]
        configured_base = base_dir or os.environ.get("CGR_ROUND_LOG_DIR")
        self.base_dir = Path(configured_base) if configured_base else repo_root / "log" / "round_logs"
        env_project_name = os.environ.get("CGR_ROUND_LOG_PROJECT")
        project_name = project_name or env_project_name or ""
        if run_name:
            self.run_name = self._sanitize_name(run_name)
        elif project_name:
            self.run_name = self._sanitize_name(f"{_DEFAULT_RUN_NAME}-{project_name}")
        elif os.environ.get("CGR_ROUND_LOG_RUN"):
            self.run_name = self._sanitize_name(os.environ.get("CGR_ROUND_LOG_RUN") or "")
        else:
            self.run_name = self._sanitize_name(_DEFAULT_RUN_NAME)
        self.run_dir = self.base_dir / self.run_name

    @staticmethod
    def _sanitize_name(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]+", "_", value.strip())
        return cleaned.strip("._-") or "unnamed"

    @staticmethod
    def _jsonable(value: Any) -> Any:
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            if isinstance(value, dict):
                return {str(k): RoundLogger._jsonable(v) for k, v in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [RoundLogger._jsonable(v) for v in value]
            return repr(value)

    @staticmethod
    def _human_text(value: Any) -> str:
        if value is None:
            return ""

        if isinstance(value, str):
            return RoundLogger._expand_literal_newlines(value)

        if isinstance(value, dict):
            if "role" in value and "content" in value:
                role = RoundLogger._expand_literal_newlines(str(value.get("role") or "message"))
                content = RoundLogger._human_text(value.get("content"))
                return f"### {role}\n\n{content}".rstrip()

            lines = []
            for key, item in value.items():
                rendered = RoundLogger._human_text(item)
                if "\n" in rendered:
                    lines.append(f"{key}:\n{rendered}")
                else:
                    lines.append(f"{key}: {rendered}")
            return "\n".join(lines).rstrip()

        if isinstance(value, (list, tuple)):
            if all(isinstance(item, dict) and "role" in item and "content" in item for item in value):
                return "\n\n".join(RoundLogger._human_text(item) for item in value).rstrip()
            return "\n\n".join(RoundLogger._human_text(item) for item in value).rstrip()

        return RoundLogger._expand_literal_newlines(str(value))

    @staticmethod
    def _expand_literal_newlines(text: str) -> str:
        if not text:
            return ""
        return (
            text.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
        )

    @staticmethod
    def _fenced_block(text: str, language: str = "text") -> str:
        text = RoundLogger._expand_literal_newlines(text or "").rstrip()
        if "```" in text:
            return f"~~~{language}\n{text}\n~~~"
        return f"```{language}\n{text}\n```"

    @classmethod
    def _estimate_tokens(cls, value: Any) -> int:
        text = cls._human_text(value)
        if not text.strip():
            return 0

        cjk_count = sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF)
        text_without_cjk = "".join(" " if 0x4E00 <= ord(ch) <= 0x9FFF else ch for ch in text)
        ascii_tokens = re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", text_without_cjk)
        return cjk_count + len(ascii_tokens)

    @classmethod
    def _token_lines(cls, request: Any, reply: Any, token_usage: Optional[Dict[str, Any]]) -> List[str]:
        usage = token_usage or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        finish_reason = usage.get("finish_reason")
        stream_diagnostics = usage.get("stream_diagnostics")
        request_options = usage.get("request_options")

        if prompt_tokens is not None or completion_tokens is not None or total_tokens is not None:
            lines = []
            if prompt_tokens is not None:
                lines.append(f"**Request Tokens:** {prompt_tokens}")
            else:
                lines.append(f"**Request Tokens:** {cls._estimate_tokens(request)} (estimated)")

            if completion_tokens is not None:
                lines.append(f"**Reply Tokens:** {completion_tokens}")
            else:
                lines.append(f"**Reply Tokens:** {cls._estimate_tokens(reply)} (estimated)")

            if total_tokens is not None:
                lines.append(f"**Total Tokens:** {total_tokens}")
            else:
                request_tokens = prompt_tokens if prompt_tokens is not None else cls._estimate_tokens(request)
                reply_tokens = completion_tokens if completion_tokens is not None else cls._estimate_tokens(reply)
                lines.append(f"**Total Tokens:** {request_tokens + reply_tokens} (estimated)")
            if finish_reason is not None:
                lines.append(f"**Finish Reason:** {finish_reason}")
            lines.extend(cls._stream_diagnostic_lines(stream_diagnostics))
            lines.extend(cls._request_option_lines(request_options))
            return lines

        request_tokens = cls._estimate_tokens(request)
        reply_tokens = cls._estimate_tokens(reply)
        lines = [
            f"**Request Tokens:** {request_tokens} (estimated)",
            f"**Reply Tokens:** {reply_tokens} (estimated)",
            f"**Total Tokens:** {request_tokens + reply_tokens} (estimated)",
        ]
        if finish_reason is not None:
            lines.append(f"**Finish Reason:** {finish_reason}")
        lines.extend(cls._stream_diagnostic_lines(stream_diagnostics))
        lines.extend(cls._request_option_lines(request_options))
        return lines

    @staticmethod
    def _stream_diagnostic_lines(stream_diagnostics: Any) -> List[str]:
        if not isinstance(stream_diagnostics, dict):
            return []
        parts = []
        finish_reasons = stream_diagnostics.get("finish_reasons")
        if finish_reasons:
            parts.append(f"finish_reasons={finish_reasons}")
        for key in (
            "event_count",
            "content_chunk_count",
            "content_chars",
            "reasoning_chunk_count",
            "reasoning_chars",
            "visible_content_empty",
        ):
            if key in stream_diagnostics:
                parts.append(f"{key}={stream_diagnostics.get(key)}")
        if not parts:
            return []
        return [f"**Stream Diagnostics:** {'; '.join(parts)}"]

    @staticmethod
    def _request_option_lines(request_options: Any) -> List[str]:
        if not isinstance(request_options, dict):
            return []
        parts = []
        for key in (
            "api_model",
            "stream",
            "max_tokens",
            "thinking_enabled",
            "thinking_disabled",
            "thinking",
            "stream_options",
            "payload_keys",
        ):
            if key in request_options:
                parts.append(f"{key}={request_options.get(key)}")
        if not parts:
            return []
        return [f"**Request Options:** {'; '.join(parts)}"]

    @classmethod
    def _thinking_block(cls, token_usage: Optional[Dict[str, Any]]) -> List[str]:
        if not isinstance(token_usage, dict):
            return []
        thinking = token_usage.get("reasoning_content")
        if not thinking:
            return []
        text = cls._expand_literal_newlines(str(thinking)).rstrip()
        if not text:
            return []
        lines = ["", "-------"]
        for line in text.splitlines():
            lines.append(f"|{line}|")
        lines.append("-------")
        return lines

    def _build_markdown_payload(
        self,
        round_index: int,
        timestamp: str,
        request: Any,
        reply: Any,
        objective: str = "",
        model_name: str = "",
        backend_name: str = "",
        call_stack: Optional[Iterable[Dict[str, Any]]] = None,
        error: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        token_usage: Optional[Dict[str, Any]] = None,
    ) -> str:
        stack_items = list(call_stack or [])
        call_source = self._format_stack_item(stack_items[0]) if stack_items else ""
        parts = [
            f"# LLM Round {round_index}",
            "",
            f"**Timestamp:** {timestamp}",
            f"**Objective:** {objective or ''}",
            f"**Model:** {model_name or ''}",
            f"**Backend:** {backend_name or ''}",
        ]

        if duration_seconds is not None:
            parts.append(f"**Duration:** {duration_seconds}s")

        parts.extend(self._token_lines(request, reply, token_usage))

        if call_source:
            parts.append(f"**Call Source:** {call_source}")

        if stack_items:
            parts.extend(["", "## CALL STACK", ""])
            for item in stack_items:
                parts.append(f"- {self._format_stack_item(item, include_context=True)}")

        if error:
            parts.extend(["", "## ERROR", "", self._fenced_block(str(error))])

        parts.extend(
            [
                "",
                "---",
                "",
                "## REQUEST",
                "",
                self._human_text(request) or "(empty)",
                "",
                "---",
                "",
                "## REPLY",
                "",
                self._human_text(reply) or "(empty)",
            ]
        )
        parts.extend(self._thinking_block(token_usage))

        return "\n".join(parts).rstrip() + "\n"

    def _format_stack_item(self, item: Dict[str, Any], include_context: bool = False) -> str:
        file_name = item.get("file", "")
        line = item.get("line", "")
        function = item.get("function", "")
        stack_line = f"{file_name}:{line} `{function}`"
        if include_context:
            context = self._expand_literal_newlines(str(item.get("code_context", "") or ""))
            if context:
                stack_line += f" - {context}"
        return stack_line

    def log_round(
        self,
        request: Any,
        reply: Any = None,
        objective: str = "",
        model_name: str = "",
        backend_name: str = "",
        call_stack: Optional[Iterable[Dict[str, Any]]] = None,
        error: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        token_usage: Optional[Dict[str, Any]] = None,
    ) -> Path:
        with _GLOBAL_LOCK:
            round_index = next(_GLOBAL_COUNTER)

        timestamp = datetime.now().isoformat(timespec="milliseconds")
        objective_for_name = self._sanitize_name(objective or "unnamed_request")[:80]
        filename = f"{round_index:06d}-{objective_for_name}.md"
        path = self.run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = self._build_markdown_payload(
            round_index=round_index,
            timestamp=timestamp,
            objective=objective,
            model_name=model_name,
            backend_name=backend_name,
            duration_seconds=duration_seconds,
            request=request,
            reply=reply,
            error=error,
            call_stack=call_stack,
            token_usage=token_usage,
        )
        path.write_text(payload, encoding="utf-8")
        return path
