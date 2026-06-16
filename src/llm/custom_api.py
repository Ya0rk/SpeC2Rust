import json
import random
import sys
import time

import requests

from llm.exceptions import ModelException


class CustomApiGen:
    """OpenAI-compatible chat completions adapter."""

    DEEPSEEK_MAX_TOKENS = 8192
    REDUCE_THINKING_PROMPT = (
        "Your previous response contained reasoning/thinking content but no visible answer. "
        "For this request, reduce hidden thinking and prioritize returning the final answer in content."
    )

    def __init__(
        self,
        api_key: str,
        model: str,
        api_base_url: str,
        max_tokens: int = 8192,
        min_interval_seconds: float = 5,
        retry_base_delay_seconds: float = 8,
        max_retries: int = 6,
        rate_limit_cooldown_seconds: float = 60,
        disable_env_proxy: bool = True,
        stream: bool = False,
    ):
        self.api_key = api_key or ""
        self.model = model
        self.api_url = self._normalize_url(api_base_url)
        self.max_tokens = int(max_tokens)
        self.min_interval_seconds = float(min_interval_seconds)
        self.retry_base_delay_seconds = float(retry_base_delay_seconds)
        self.max_retries = int(max_retries)
        self.rate_limit_cooldown_seconds = float(rate_limit_cooldown_seconds)
        self.disable_env_proxy = bool(disable_env_proxy)
        self.stream = bool(stream)
        self.enable_thinking = "deepseek" in (self.model or "").lower()
        self.disable_thinking = False
        self._last_request_time = 0.0
        self._current_max_tokens = self.max_tokens
        self._remind_reduce_thinking = False
        self._current_request_label = ""
        self.last_usage = None
        self.last_stream_diagnostics = None
        self.last_request_metadata = None
        self.last_sanitized_surrogates = 0
        self.session = requests.Session()
        # 某些环境会注入 HTTP(S)_PROXY，导致兼容 API 走到不稳定代理链路。
        # 对直连模型服务的场景，默认禁用 requests 对环境代理变量的继承。
        self.session.trust_env = not self.disable_env_proxy

    def _normalize_url(self, api_base_url: str) -> str:
        if not api_base_url:
            raise ValueError("api_base_url is required for custom_api model")

        normalized = api_base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        if normalized.endswith("/v1") or normalized.endswith("/v4"):
            return normalized + "/chat/completions"
        return normalized + "/v1/chat/completions"

    def _wait_for_min_interval(self):
        # 控制请求节奏，避免短时间内连续访问 API。
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval_seconds:
            wait_seconds = self.min_interval_seconds - elapsed
            print(f"Custom API rate control: waiting {wait_seconds:.1f}s before next request...")
            time.sleep(wait_seconds)

    def _build_retry_delay(self, retry_count: int, is_rate_limited: bool) -> float:
        # 限流时优先使用更长冷却时间；其他错误使用指数退避并附带轻微抖动。
        if is_rate_limited:
            base_delay = max(
                self.rate_limit_cooldown_seconds,
                self.retry_base_delay_seconds * (2 ** max(0, retry_count - 1)),
            )
        else:
            base_delay = self.retry_base_delay_seconds * (2 ** max(0, retry_count - 1))

        jitter = random.uniform(0, min(3.0, max(1.0, base_delay * 0.1)))
        return base_delay + jitter

    def set_request_label(self, label: str):
        self._current_request_label = (label or "").strip()

    @staticmethod
    def _decode_stream_line(raw_line) -> str:
        if isinstance(raw_line, bytes):
            return raw_line.decode("utf-8", errors="replace")
        return raw_line

    @staticmethod
    def _text_metrics(text: str) -> tuple[int, int, int]:
        mojibake_markers = sum(text.count(marker) for marker in ("Ã", "Â", "â", "ð", "è", "ä", "æ", "å", "ç", "é"))
        control_chars = sum(
            1
            for ch in text
            if ord(ch) < 32 and ch not in "\r\n\t" or 0x80 <= ord(ch) <= 0x9F
        )
        cjk_chars = sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF)
        return mojibake_markers, control_chars, cjk_chars

    @classmethod
    def _has_mojibake_signals(cls, text: str) -> bool:
        markers, controls, _ = cls._text_metrics(text)
        return markers > 0 or controls > 0

    @classmethod
    def _repair_mojibake_text(cls, text: str) -> str:
        if not text:
            return text

        best_text = text
        for _ in range(3):
            if not cls._has_mojibake_signals(best_text):
                break

            try:
                repaired = best_text.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break

            original_markers, original_controls, original_cjk = cls._text_metrics(best_text)
            repaired_markers, repaired_controls, repaired_cjk = cls._text_metrics(repaired)

            original_noise = original_markers + original_controls
            repaired_noise = repaired_markers + repaired_controls
            if repaired_noise < original_noise and repaired_cjk >= original_cjk:
                best_text = repaired
                continue
            break

        return best_text

    def _stream_response_content(self, response) -> str:
        """
        解析 OpenAI-compatible SSE 流，并实时打印增量内容。
        """
        chunks = []
        reasoning_chunks = []
        finish_reasons = []
        event_count = 0
        content_chunk_count = 0
        reasoning_chunk_count = 0
        started = False

        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue

            line = self._decode_stream_line(raw_line).strip()
            if not line.startswith("data:"):
                continue

            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break

            try:
                event = json.loads(data)
            except Exception:
                continue

            event_count += 1
            if event.get("usage"):
                self.last_usage = event.get("usage")

            choices = event.get("choices") or []
            if not choices:
                continue

            for choice in choices:
                finish_reason = choice.get("finish_reason")
                if finish_reason is not None:
                    finish_reasons.append(str(finish_reason))

                delta = choice.get("delta") or {}
                message = choice.get("message") or {}

                reasoning_piece = self._coerce_content_piece(
                    delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("thinking")
                    or message.get("reasoning_content")
                    or message.get("reasoning")
                    or message.get("thinking")
                )
                if reasoning_piece:
                    reasoning_chunk_count += 1
                    reasoning_chunks.append(reasoning_piece)

                piece = self._coerce_content_piece(
                    delta.get("content")
                    or delta.get("text")
                    or message.get("content")
                    or choice.get("text")
                )
                if not piece:
                    continue

                content_chunk_count += 1
                if not started:
                    stream_title = self._current_request_label or "unnamed request"
                    print(f"\n[stream start] {stream_title}")
                    started = True

                sys.stdout.write(piece)
                sys.stdout.flush()
                chunks.append(piece)

        if started:
            stream_title = self._current_request_label or "unnamed request"
            print(f"\n[stream end] {stream_title}")

        body = self._repair_mojibake_text("".join(chunks))
        reasoning_text = "".join(reasoning_chunks)
        self.last_stream_diagnostics = {
            "event_count": event_count,
            "content_chunk_count": content_chunk_count,
            "content_chars": len(body),
            "reasoning_chunk_count": reasoning_chunk_count,
            "reasoning_chars": len(reasoning_text),
            "finish_reasons": finish_reasons,
            "visible_content_empty": not bool(body.strip()),
        }
        if self.last_usage is None:
            self.last_usage = {}
        if isinstance(self.last_usage, dict):
            if finish_reasons:
                self.last_usage["finish_reason"] = finish_reasons[-1]
                self.last_usage["finish_reasons"] = finish_reasons
            self.last_usage["stream_diagnostics"] = self.last_stream_diagnostics
            if reasoning_text:
                self.last_usage["reasoning_content"] = reasoning_text
        return body

    @staticmethod
    def _coerce_content_piece(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(
                        str(
                            item.get("text")
                            or item.get("content")
                            or item.get("value")
                            or ""
                        )
                    )
                elif item is not None:
                    parts.append(str(item))
            return "".join(parts)
        return str(value)

    def _reasoning_content_from_choice(self, choice) -> str:
        if not isinstance(choice, dict):
            return ""
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        return self._coerce_content_piece(
            message.get("reasoning_content")
            or message.get("reasoning")
            or message.get("thinking")
            or choice.get("reasoning_content")
            or choice.get("reasoning")
            or choice.get("thinking")
        )

    def _request_max_tokens(self) -> int:
        if self.enable_thinking:
            return self.DEEPSEEK_MAX_TOKENS
        return self._current_max_tokens

    def _messages_with_reduce_thinking_prompt(self, messages):
        if not (self.enable_thinking and self._remind_reduce_thinking):
            return messages

        prompt = {"role": "user", "content": self.REDUCE_THINKING_PROMPT}
        if isinstance(messages, list):
            return messages + [prompt]
        return [prompt, {"role": "user", "content": str(messages)}]

    def _update_reduce_thinking_reminder(self, content: str, reasoning_text: str):
        self._remind_reduce_thinking = bool(reasoning_text) and not bool((content or "").strip())

    def _build_payload(self, messages, temperature):
        self.last_sanitized_surrogates = 0
        request_messages = self._messages_with_reduce_thinking_prompt(messages)
        payload = {
            "model": self.model,
            "messages": self._sanitize_json_value(request_messages),
            "temperature": temperature,
            "max_tokens": self._request_max_tokens(),
            "stream": self.stream,
        }
        if self.enable_thinking:
            payload["thinking"] = {"type": "enabled"}
        if self.stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _sanitize_json_value(self, value):
        if isinstance(value, str):
            return self._sanitize_json_string(value)
        if isinstance(value, list):
            return [self._sanitize_json_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_json_value(item) for item in value]
        if isinstance(value, dict):
            return {
                self._sanitize_json_string(str(key)): self._sanitize_json_value(item)
                for key, item in value.items()
            }
        return value

    def _sanitize_json_string(self, text: str) -> str:
        if not text:
            return text
        bad = 0
        chars = []
        for ch in text:
            if 0xD800 <= ord(ch) <= 0xDFFF:
                bad += 1
                chars.append("\uFFFD")
            else:
                chars.append(ch)
        if bad:
            self.last_sanitized_surrogates += bad
            return "".join(chars)
        return text

    def _request_metadata(self, payload):
        metadata = {
            "api_model": self.model,
            "stream": self.stream,
            "max_tokens": payload.get("max_tokens"),
            "thinking_enabled": self.enable_thinking,
            "thinking_disabled": self.disable_thinking,
            "reduce_thinking_prompt_added": bool(
                self.enable_thinking and self._remind_reduce_thinking
            ),
            "sanitized_surrogates": self.last_sanitized_surrogates,
            "payload_keys": sorted(payload.keys()),
        }
        if "thinking" in payload:
            metadata["thinking"] = payload.get("thinking")
        if "stream_options" in payload:
            metadata["stream_options"] = payload.get("stream_options")
        return metadata

    def _merge_request_metadata(self):
        if self.last_usage is None:
            self.last_usage = {}
        if isinstance(self.last_usage, dict) and self.last_request_metadata:
            self.last_usage["request_options"] = self.last_request_metadata

    @staticmethod
    def _serialize_payload(payload) -> bytes:
        """Serialize request JSON after local normalization.

        We intentionally own this serialization step instead of delegating to
        requests' ``json=`` shortcut so invalid Unicode cannot silently become
        a server-side JSON parse error.
        """
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")

    def get_response(self, messages, temperature=0, top_k=1):
        if top_k != 1 and temperature == 0:
            raise ModelException("Top k sampling requires a non-zero temperature")

        self._current_max_tokens = self.max_tokens

        headers = {
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        retry_count = 0
        response_end_prematurely_count = 0

        while True:
            try:
                self._wait_for_min_interval()
                self.last_usage = None
                self.last_stream_diagnostics = None
                self.last_request_metadata = None

                payload = self._build_payload(messages, temperature)
                self.last_request_metadata = self._request_metadata(payload)
                request_body = self._serialize_payload(payload)
                response = self.session.post(
                    self.api_url,
                    data=request_body,
                    headers=headers,
                    timeout=180,
                    stream=self.stream,
                )
                self._last_request_time = time.time()
                response.encoding = "utf-8"

                if response.status_code != 200:
                    try:
                        detail = response.json()
                    except Exception:
                        detail = response.text
                    raise ModelException(
                        f"Custom API request failed: status={response.status_code}, detail={detail}"
                    )

                if self.stream:
                    body_content = self._stream_response_content(response)
                    reasoning_text = ""
                    if isinstance(self.last_usage, dict):
                        reasoning_text = str(self.last_usage.get("reasoning_content") or "")
                    self._update_reduce_thinking_reminder(body_content, reasoning_text)
                    self._merge_request_metadata()
                    response_end_prematurely_count = 0
                    return [body_content]

                body = response.json()
                response_end_prematurely_count = 0
                self.last_usage = body.get("usage")
                choice = body["choices"][0]
                if isinstance(self.last_usage, dict) and choice.get("finish_reason") is not None:
                    self.last_usage["finish_reason"] = choice.get("finish_reason")
                reasoning_text = self._reasoning_content_from_choice(choice)
                if reasoning_text:
                    if self.last_usage is None:
                        self.last_usage = {}
                    if isinstance(self.last_usage, dict):
                        self.last_usage["reasoning_content"] = reasoning_text
                self._merge_request_metadata()
                content = self._coerce_content_piece((choice.get("message") or {}).get("content"))
                content = self._repair_mojibake_text(content)
                self._update_reduce_thinking_reminder(content, reasoning_text)
                return [content]
            except Exception as e:
                retry_count += 1
                if self.max_retries > 0 and retry_count >= self.max_retries:
                    raise ModelException(f"Custom API failed after retries: {str(e)}")

                error_text = str(e)
                lowered_error = error_text.lower()
                is_rate_limited = (
                    "429" in error_text
                    or "rate limit" in lowered_error
                    or "too many requests" in lowered_error
                )
                is_proxy_or_tls_error = (
                    "proxyerror" in lowered_error
                    or "unable to connect to proxy" in lowered_error
                    or "remotedisconnected" in lowered_error
                    or "ssleoferror" in lowered_error
                    or "unexpected eof while reading" in lowered_error
                    or "ssl:" in lowered_error
                )

                if "response ended prematurely" in lowered_error:
                    response_end_prematurely_count += 1
                    if response_end_prematurely_count >= 3 and self._current_max_tokens > 2048:
                        next_max_tokens = max(2048, self._current_max_tokens // 2)
                        if next_max_tokens < self._current_max_tokens:
                            print(
                                f"Custom API long-response instability detected after {response_end_prematurely_count} premature endings. "
                                f"Reducing max_tokens from {self._current_max_tokens} to {next_max_tokens} for subsequent retries."
                            )
                            self._current_max_tokens = next_max_tokens

                delay_seconds = self._build_retry_delay(retry_count, is_rate_limited)
                retry_label = (
                    f"{retry_count}/{self.max_retries}"
                    if self.max_retries > 0
                    else f"{retry_count}/inf"
                )
                if is_proxy_or_tls_error:
                    print(
                        f"Custom API proxy/TLS error: {error_text}. "
                        f"Retrying in {delay_seconds:.1f}s ({retry_label})..."
                    )
                elif is_rate_limited:
                    print(
                        f"Custom API rate limit detected: {error_text}. "
                        f"Cooling down for {delay_seconds:.1f}s before retry {retry_label}..."
                    )
                else:
                    print(
                        f"Custom API error: {error_text}. "
                        f"Retrying in {delay_seconds:.1f}s ({retry_label})..."
                    )

                time.sleep(delay_seconds)
