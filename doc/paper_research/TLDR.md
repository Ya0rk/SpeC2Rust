# TLDR

本文件总结 `doc/paper_research` 根目录中的入口文档，并给出子目录导航。各子目录还有自己的 `TLDR.md`，用于快速判断该目录下每个文件在讲什么。

## 文件

| 文件 | 讲什么 |
| --- | --- |
| [README.md](README.md) | 论文调研文档总入口，说明目录结构、建议阅读顺序、写作规范和这些材料如何支撑论文方法、系统实现、实验与局限分析。 |

## 目录

| 目录 | 讲什么 |
| --- | --- |
| [00_index](00_index/TLDR.md) | 研究地图、术语表、论点与证据矩阵。 |
| [01_pipeline_orchestration](01_pipeline_orchestration/TLDR.md) | 端到端脚本入口、`main.py` 主流程、可复现日志。 |
| [02_c_program_understanding](02_c_program_understanding/TLDR.md) | C 程序静态分析、模块切分、规格文档和指针 / 宏证据。 |
| [03_context_construction](03_context_construction/TLDR.md) | Spec 上下文索引、迁移契约、prompt 材料预算和轮次日志。 |
| [04_rust_generation](04_rust_generation/TLDR.md) | ContextualRustAgent、文件规划、符号读取协议和生成器 baseline。 |
| [05_compile_repair](05_compile_repair/TLDR.md) | 编译修复闭环、错误前沿、错误分批、结构修复和分层信息注入。 |
| [06_functional_testing](06_functional_testing/TLDR.md) | rtest、TestRunner、快照回归、LogAgent、probe 和逐步场景。 |
| [07_metrics_and_evaluation](07_metrics_and_evaluation/TLDR.md) | 数据集、成功指标、消融计划、失败分类、成本与延迟。 |
| [08_engineering_optimizations](08_engineering_optimizations/TLDR.md) | 环境隔离、上下文去重、输出契约、安全守卫。 |
| [09_paper_materials](09_paper_materials/TLDR.md) | 方法章节提纲、系统图、论文表格和相关工作映射。 |
