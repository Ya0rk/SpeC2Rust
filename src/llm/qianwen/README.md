# llm/qianwen 目录说明

## 职责

这个子目录实现本地 Qwen 模型服务的 HTTP 适配，是当前工程里实际工作的 LLM 后端。

## 文件说明

- [`qianwen_gen.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/qianwen/qianwen_gen.py): `QwenLocalGen`，向本地兼容 OpenAI Chat Completions 的接口发送请求。
- [`__init__.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/qianwen/__init__.py): 导出 `QwenLocalGen`。

## 工作方式

[`qianwen_gen.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/qianwen/qianwen_gen.py) 默认请求：

- 地址：`http://0.0.0.0:8080/v1/chat/completions`
- 输入：`messages`
- 输出：`choices[0].message.content`

附带了：

- Bearer Token 鉴权头
- 简单重试
- `top_k` 的递归采样接口雏形

## 关键 trick

- 通过本地推理服务把“模型调用”变成稳定的外部依赖，让上层代码只关心 prompt 和返回文本。
- 用重试逻辑处理本地模型偶发失败，这对长流水线很重要。

## 当前限制

- API 地址被硬编码。
- 超时控制注释掉了，长请求可能阻塞。
- `top_k > 1` 的实现递归调用 `self.gen(...)`，但类中没有 `gen` 方法，属于潜在 bug。
