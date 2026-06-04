# TLDR

本目录面向实验章节，定义数据集、成功指标、消融、失败分类以及成本 / 延迟统计方式。

| 文件 | 讲什么 |
| --- | --- |
| [01_dataset_inventory.md](01_dataset_inventory.md) | 盘点数据集、输入 C 项目、中间产物、输出 Rust 项目和可统计日志，为实验样本管理提供基础。 |
| [02_success_metrics.md](02_success_metrics.md) | 定义翻译成功的层级指标，包括生成完成、`cargo check`、release build、功能测试通过和回归保护结果。 |
| [03_ablation_plan.md](03_ablation_plan.md) | 设计消融实验矩阵，覆盖 Spec、ContextualRustAgent、错误分批、C/spec 材料、LogAgent、回归检查等系统组件。 |
| [04_failure_taxonomy.md](04_failure_taxonomy.md) | 建立失败分类体系，把失败归因到解析、上下文、生成、编译修复、测试行为、环境和协议等类别。 |
| [05_cost_and_latency.md](05_cost_and_latency.md) | 定义成本与延迟指标，包括 LLM 调用次数、轮数、prompt / completion 规模、构建耗时、测试耗时和整体流水线耗时。 |
