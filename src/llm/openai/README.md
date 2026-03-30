# llm/openai 目录说明

## 职责

这个子目录预留给 OpenAI 后端适配。

## 文件说明

- [`oai.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/openai/oai.py): `OpenAiGen` 占位实现。
- [`__init__.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/openai/__init__.py): 导出 `OpenAiGen`。

## 当前状态

[`oai.py`](/E:/Code/C2R-Auto/cGrcode/src/llm/openai/oai.py) 中真正的 API 调用逻辑被注释掉了，`get_response` 也没有完成，因此该目录目前更像接口草稿，而不是生产可用实现。

## 研究意义

这个目录说明项目原本希望同时支持：

- 本地模型服务
- 云端商用模型

但当前实验重心明显已经偏向本地 Qwen 服务。
