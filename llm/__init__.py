from .qianwen.qianwen_gen import QwenLocalGen
from .openai.oai import OpenAiGen

__all__ = [
    "QwenLocalGen",
    "OpenAiGen"
]

def get_model(model_name: str):
    if model_name == "qwen7":
        return QwenLocalGen(api_key="tcode-12345", model="Qwen2.5-Coder-7B-Instruct")
    elif model_name == "qianwen14":
        return QwenLocalGen(api_key="tcode-12345", model = "Qwen2.5-Coder-14B-Instruct")
    elif model_name == "oai":
        return OpenAiGen(model="gpt-3.5-turbo")
    else:
        raise ValueError(f"Unknown model name: {model_name}")