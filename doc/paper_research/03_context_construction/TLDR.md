# TLDR

本目录说明翻译和修复阶段的上下文如何组织、检索、预算化和记录。

| 文件 | 讲什么 |
| --- | --- |
| [01_spec_context_indexing.md](01_spec_context_indexing.md) | 解释 Spec 文档如何被索引和按需检索，让生成 / 修复 agent 能读取相关规格而不是一次性加载全部文档。 |
| [02_translation_contract.md](02_translation_contract.md) | 分析 `translation_contract.json` 如何描述迁移范围、入口、模块、外部依赖和可编辑边界，是上下文构造的机器契约。 |
| [03_prompt_budget_and_material_policy.md](03_prompt_budget_and_material_policy.md) | 总结 prompt 预算、材料注入、材料裁剪和预算压力管理策略，说明系统如何避免上下文过载。 |
| [04_round_logging_and_metrics.md](04_round_logging_and_metrics.md) | 说明 LLM 轮次日志、输入输出、材料请求、编辑记录和指标字段如何落盘，便于复盘与论文实验统计。 |
