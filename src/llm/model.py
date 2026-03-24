import sys
from pathlib import Path
from .qianwen.qianwen_gen import QwenLocalGen
from .openai.oai import OpenAiGen

sys.path.append(str(Path(__file__).parent.parent))
from config.config import Config

class Model:
    def __init__(self, config: Config):
        self.config = config
        self.model_name = config.model_name
        self.llm = self._get_model(config.model_name, config.api_key)    

    def generate(self, prompt: str):
        return self.llm.get_response(prompt)
        
    def _get_model(self, model_name: str, api_key: str):
        if model_name == "qwen7":
            return QwenLocalGen(api_key=api_key, model="Qwen2.5-Coder-7B-Instruct")
        elif model_name == "qianwen14":
            return QwenLocalGen(api_key=api_key, model = "Qwen2.5-Coder-14B-Instruct")
        elif model_name == "qwen32":
            return QwenLocalGen(api_key=api_key, model = "Qwen2.5-Coder-32B-Instruct")
        elif model_name == "oai":
            return OpenAiGen(api_key=api_key, model="gpt-3.5-turbo")
        else:
            raise ValueError(f"Unknown model name: {model_name}")