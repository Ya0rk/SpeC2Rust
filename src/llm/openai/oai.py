from llm.custom_api import CustomApiGen


class OpenAiGen(CustomApiGen):
    def __init__(
        self,
        api_key,
        model="gpt-3.5-turbo",
        api_base_url="https://api.openai.com/v1",
        max_tokens=512,
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            api_base_url=api_base_url,
            max_tokens=max_tokens,
        )
