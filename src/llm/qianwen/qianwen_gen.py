import time
import requests
from llm.exceptions import ModelException


class QwenLocalGen:
    def __init__(self, api_key, model):
        """
        初始化本地Qwen模型服务地址和鉴权信息
        
        :param api_url: vLLM服务的根地址（如"http://localhost:8080"）
        :param api_key: 本地服务设置的API Key（若部署时用了--api-key则需传入）
        """
        self.api_url = "http://0.0.0.0:8080/v1/chat/completions"
        # self.api_url = "http://10.249.42.56:8000/v1/chat/completions"
        self.model = model
        self.api_key = api_key
        self.last_usage = None

    def get_response(self, messages, temperature=0, top_k=1):
        """
        生成文本（保持与原ClaudeGen一致的输入输出格式）
        
        :param messages: 对话历史（格式同原ClaudeGen，支持system/user/assistant角色）
        :param temperature: 生成温度（0表示确定性生成）
        :param top_k: 生成结果数量（需temperature>0时生效）
        :return: 生成的文本列表（长度等于top_k）
        """
        # 参数校验（与原ClaudeGen逻辑一致）
        
        if top_k != 1 and temperature == 0:
            raise ModelException("Top k sampling requires a non-zero temperature")
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096
        }
        headers = {
            # "api-key": self.api_key,
            "Authorization": f"Bearer {self.api_key}",  # qianwen标准格式
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }

        retry_count = 0
        while True:
            try:
                self.last_usage = None
                response = requests.post(
                    url=self.api_url,
                    json=payload,
                    headers=headers,
                    # timeout=60  # 增加超时时间避免长时间阻塞
                )
                # 处理非200响应
                if response.status_code != 200:
                    raise ModelException(response.json()['error']['message'])
                
                # 解析响应：提取所有生成结果（vLLM的choices列表对应top_k个结果）
                response = response.json()
                self.last_usage = response.get("usage")
                # print(json.dumps(response, indent=2, ensure_ascii=False))
                # pause()
                # print(response['choices'][0]['message']['content'])
                # print(len(response['choices'][0]['message']['content']))
                # if len(response['choices'][0]['message']['content']) > 1:
                    # raise ModelException("Qianwen returned multiple response")
                break
            except Exception as e:
                retry_count += 1
                if retry_count >= 5:
                    raise ModelException(f"Qwen Local API Failed After Retries: {str(e)}")
                print(f"Qwen Local API Error: {str(e)}. Retrying in 10 seconds...")
                time.sleep(10)

        if top_k > 1:
            return [response['choices'][0]['message']['content']] + self.gen(messages, temperature, top_k=top_k - 1)
        else:
            return [response['choices'][0]['message']['content']]
