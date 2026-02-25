class Config:
    def __init__(self, config_path=None):
        # 默认配置
        self.api_key = "tcode-12345"  # 与scripts/qwen.sh中的API key保持一致
        self.model = "Qwen2.5-Coder-7B-Instruct"  # 默认使用7B模型
        
        # 如果提供了配置文件路径，则加载配置
        if config_path:
            self._load_config(config_path)
    
    def _load_config(self, config_path):
        """加载配置文件"""
        try:
            import json
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            if 'api_key' in config:
                self.api_key = config['api_key']
            if 'model' in config:
                self.model = config['model']
        except Exception as e:
            print(f"加载配置文件失败: {e}")
