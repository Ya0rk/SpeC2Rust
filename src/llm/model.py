import inspect
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from .custom_api import CustomApiGen
from .openai.oai import OpenAiGen
from .qianwen.qianwen_gen import QwenLocalGen
from utils.translation_metrics import translation_metrics
from utils.round_logger import RoundLogger
from config.config import Config


class Model:
    def __init__(self, config: Config):
        self.config = config
        self.model_name = config.model_name
        self.llm = self._get_model(config)
        self._current_request_label = ""
        self.round_logger = RoundLogger(base_dir=getattr(config, "round_log_dir", ""))

    def generate(self, prompt: str):
        translation_metrics.increment_llm_requests()
        started_at = time.time()
        call_stack = self._capture_generate_stack()
        reply = None
        error = None
        try:
            reply = self.llm.get_response(prompt)
            return reply
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if self._round_log_enabled():
                self._safe_log_round(
                    request=prompt,
                    reply=reply,
                    error=error,
                    call_stack=call_stack,
                    duration_seconds=time.time() - started_at,
                )

    def set_request_label(self, label: str):
        self._current_request_label = (label or "").strip()
        if hasattr(self.llm, "set_request_label"):
            self.llm.set_request_label(label)

    def _round_log_enabled(self) -> bool:
        return bool(getattr(self.config, "round_log_enabled", True))

    def _infer_objective(self, request) -> str:
        if self._current_request_label:
            return self._current_request_label
        if isinstance(request, list):
            for message in request:
                if not isinstance(message, dict):
                    continue
                content = str(message.get("content") or "").strip()
                if content:
                    return content.splitlines()[0][:120]
        return "unnamed request"

    def _capture_generate_stack(self):
        repo_root = Path(__file__).resolve().parents[2]
        frames = []
        for frame in inspect.stack():
            path = Path(frame.filename)
            try:
                file_name = str(path.resolve().relative_to(repo_root))
            except Exception:
                file_name = str(path)
            frames.append(
                {
                    "file": file_name.replace("\\", "/"),
                    "line": frame.lineno,
                    "function": frame.function,
                    "code_context": (frame.code_context[0].strip() if frame.code_context else ""),
                }
            )
        return frames

    def _safe_log_round(self, request, reply, error, call_stack, duration_seconds: float):
        try:
            logger = getattr(self, "round_logger", None) or RoundLogger()
            logger.log_round(
                request=request,
                reply=reply,
                objective=self._infer_objective(request),
                model_name=self.model_name,
                backend_name=type(self.llm).__name__,
                call_stack=call_stack,
                error=error,
                duration_seconds=round(duration_seconds, 3),
                token_usage=getattr(self.llm, "last_usage", None),
            )
        except Exception as log_error:
            print(f"Round log write failed: {log_error}")

    def _get_model(self, config: Config):
        model_name = config.model_name
        api_key = config.api_key

        if model_name == "qwen7":
            return QwenLocalGen(api_key=api_key, model="Qwen2.5-Coder-7B-Instruct")
        if model_name in ("qwen14", "qianwen14"):
            return QwenLocalGen(api_key=api_key, model="Qwen2.5-Coder-14B-Instruct")
        if model_name == "qwen32":
            return QwenLocalGen(api_key=api_key, model="Qwen2.5-Coder-32B-Instruct")
        if model_name == "oai":
            return OpenAiGen(
                api_key=api_key,
                model=config.api_model or "gpt-3.5-turbo",
                api_base_url=config.api_base_url or "https://api.openai.com/v1",
                max_tokens=config.api_max_tokens,
            )
        if model_name == "custom_api":
            return CustomApiGen(
                api_key=api_key,
                model=config.api_model or "gpt-4o-mini",
                api_base_url=config.api_base_url,
                max_tokens=config.api_max_tokens,
                min_interval_seconds=config.api_min_interval_seconds,
                retry_base_delay_seconds=config.api_retry_base_delay_seconds,
                max_retries=config.api_max_retries,
                rate_limit_cooldown_seconds=config.api_rate_limit_cooldown_seconds,
                disable_env_proxy=config.api_disable_env_proxy,
                stream=config.api_stream,
            )
        raise ValueError(f"Unknown model name: {model_name}")
