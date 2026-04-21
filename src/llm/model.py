import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from .custom_api import CustomApiGen
from .openai.oai import OpenAiGen
from .qianwen.qianwen_gen import QwenLocalGen
from utils.translation_metrics import translation_metrics
from config.config import Config


class Model:
    def __init__(self, config: Config):
        self.config = config
        self.model_name = config.model_name
        self.llm = self._get_model(config)

    def generate(self, prompt: str):
        translation_metrics.increment_llm_requests()
        return self.llm.get_response(prompt)

    def set_request_label(self, label: str):
        if hasattr(self.llm, "set_request_label"):
            self.llm.set_request_label(label)

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
