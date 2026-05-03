class Config:
    def __init__(
        self,
        config_path=None,
        model_name="qwen32",
        api_key=None,
        api_base_url=None,
        api_model=None,
        api_max_tokens=8192,
        api_min_interval_seconds=5,
        api_retry_base_delay_seconds=8,
        api_max_retries=6,
        api_rate_limit_cooldown_seconds=60,
        api_disable_env_proxy=True,
        api_stream=False,
        rag_enabled=False,
        rag_top_k=4,
        generate_tests=False,
        generate_examples=False,
        generate_benches=False,
        skeleton_first=True,
        round_log_enabled=True,
        round_log_dir="",
    ):
        self.api_key = api_key or "tcode-12345"
        self.model_name = model_name
        self.api_base_url = api_base_url or ""
        self.api_model = api_model or ""
        self.api_max_tokens = int(api_max_tokens)
        self.api_min_interval_seconds = float(api_min_interval_seconds)
        self.api_retry_base_delay_seconds = float(api_retry_base_delay_seconds)
        self.api_max_retries = int(api_max_retries)
        self.api_rate_limit_cooldown_seconds = float(api_rate_limit_cooldown_seconds)
        self.api_disable_env_proxy = bool(api_disable_env_proxy)
        self.api_stream = bool(api_stream)
        self.rag_enabled = bool(rag_enabled)
        self.rag_top_k = int(rag_top_k)
        self.generate_tests = bool(generate_tests)
        self.generate_examples = bool(generate_examples)
        self.generate_benches = bool(generate_benches)
        self.skeleton_first = bool(skeleton_first)
        self.round_log_enabled = bool(round_log_enabled)
        self.round_log_dir = round_log_dir or ""

        if config_path:
            self._load_config(config_path)

    def _load_config(self, config_path):
        try:
            import json

            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            if "api_key" in config:
                self.api_key = config["api_key"]
            if "model_name" in config:
                self.model_name = config["model_name"]
            if "api_base_url" in config:
                self.api_base_url = config["api_base_url"]
            if "api_model" in config:
                self.api_model = config["api_model"]
            if "api_max_tokens" in config:
                self.api_max_tokens = int(config["api_max_tokens"])
            if "api_min_interval_seconds" in config:
                self.api_min_interval_seconds = float(config["api_min_interval_seconds"])
            if "api_retry_base_delay_seconds" in config:
                self.api_retry_base_delay_seconds = float(config["api_retry_base_delay_seconds"])
            if "api_max_retries" in config:
                self.api_max_retries = int(config["api_max_retries"])
            if "api_rate_limit_cooldown_seconds" in config:
                self.api_rate_limit_cooldown_seconds = float(config["api_rate_limit_cooldown_seconds"])
            if "api_disable_env_proxy" in config:
                self.api_disable_env_proxy = bool(config["api_disable_env_proxy"])
            if "api_stream" in config:
                self.api_stream = bool(config["api_stream"])
            if "rag_enabled" in config:
                self.rag_enabled = bool(config["rag_enabled"])
            if "rag_top_k" in config:
                self.rag_top_k = int(config["rag_top_k"])
            if "generate_tests" in config:
                self.generate_tests = bool(config["generate_tests"])
            if "generate_examples" in config:
                self.generate_examples = bool(config["generate_examples"])
            if "generate_benches" in config:
                self.generate_benches = bool(config["generate_benches"])
            if "skeleton_first" in config:
                self.skeleton_first = bool(config["skeleton_first"])
            if "round_log_enabled" in config:
                self.round_log_enabled = bool(config["round_log_enabled"])
            if "round_log_dir" in config:
                self.round_log_dir = config["round_log_dir"] or ""
        except Exception as e:
            print(f"加载本地 API 配置失败: {e}")
