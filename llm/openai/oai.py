import openai
import os

class OpenAiGen:
    def init(self):
        pass

    def get_response(messages, model="gpt-3.5-turbo", temperature=0.0, max_tokens=4096):
        pass
        # 设置 API 密钥
        # openai.api_key = os.getenv("OPENAI_API_KEY")
        
        # try:
        #     # 调用 OpenAI API
        #     response = openai.ChatCompletion.create(
        #         model=model,
        #         messages=messages,
        #         temperature=temperature,
        #         max_tokens=max_tokens
        #     )
            
        #     # 提取并返回模型的回复
        #     return response["choices"][0]["message"]["content"]
        # except Exception as e:
        #     return f"错误: {str(e)}"