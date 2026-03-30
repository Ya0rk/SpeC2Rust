import random
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
    ):
        self.api_key = api_key or ""
        self.model = model
        self.api_url = self._normalize_url(api_base_url)
        self.max_tokens = int(max_tokens)
        self.min_interval_seconds = float(min_interval_seconds)
        self.retry_base_delay_seconds = float(retry_base_delay_seconds)
        self.max_retries = int(max_retries)
        self.rate_limit_cooldown_seconds = float(rate_limit_cooldown_seconds)
        self._last_request_time = 0.0

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

    def get_response(self, messages, temperature=0, top_k=1):
        if top_k != 1 and temperature == 0:
            raise ModelException("Top k sampling requires a non-zero temperature")

        headers = {
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        retry_count = 0
        current_max_tokens = self.max_tokens

        while True:
            try:
                self._wait_for_min_interval()

                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": current_max_tokens,
                    "stream": False,
                }
                response = requests.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=180,
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

                body = response.json()
                return [body["choices"][0]["message"]["content"]]
            except Exception as e:
                retry_count += 1
                if retry_count >= self.max_retries:
                    raise ModelException(f"Custom API failed after retries: {str(e)}")

                error_text = str(e)
                is_rate_limited = (
                    "429" in error_text
                    or "rate limit" in error_text.lower()
                    or "too many requests" in error_text.lower()
                    or "max retries exceeded" in error_text.lower()
                )

                if "Response ended prematurely" in error_text and current_max_tokens > 512:
                    current_max_tokens = max(512, current_max_tokens // 2)
                    print(
                        f"Custom API error: {error_text}. "
                        f"Reducing max_tokens to {current_max_tokens} before retry."
                    )

                delay_seconds = self._build_retry_delay(retry_count, is_rate_limited)
                if is_rate_limited:
                    print(
                        f"Custom API rate limit detected: {error_text}. "
                        f"Cooling down for {delay_seconds:.1f}s before retry {retry_count}/{self.max_retries}..."
                    )
                else:
                    print(
                        f"Custom API error: {error_text}. "
                        f"Retrying in {delay_seconds:.1f}s ({retry_count}/{self.max_retries})..."
                    )

                time.sleep(delay_seconds)
