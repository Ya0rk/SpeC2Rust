import json
import random
import sys
import time

import requests

from llm.exceptions import ModelException


class CustomApiGen:
    """OpenAI-compatible chat completions adapter."""

    def __init__(
        self,
        api_key: str,
        model: str,
        api_base_url: str,
        max_tokens: int = 896,
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
        self._last_request_time = 0.0
        self._current_max_tokens = self.max_tokens
        self._current_request_label = ""
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

    def _stream_response_content(self, response) -> str:
        """
        解析 OpenAI-compatible SSE 流，并实时打印增量内容。
        """
        chunks = []
        started = False

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue

            line = raw_line.strip()
            if not line.startswith("data:"):
                continue

            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break

            try:
                event = json.loads(data)
            except Exception:
                continue

            choices = event.get("choices") or []
            if not choices:
                continue

            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or ""
            if not piece:
                continue

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

        return "".join(chunks)

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

                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": self._current_max_tokens,
                    "stream": self.stream,
                }
                response = self.session.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=180,
                    stream=self.stream,
                )
                self._last_request_time = time.time()

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
                    response_end_prematurely_count = 0
                    return [body_content]

                body = response.json()
                response_end_prematurely_count = 0
                return [body["choices"][0]["message"]["content"]]
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
