# TLDR

本目录覆盖 Rust 初始生成阶段，重点是按需上下文、文件规划、符号读取和 baseline 生成器对照。

| 文件 | 讲什么 |
| --- | --- |
| [01_contextual_rust_agent.md](01_contextual_rust_agent.md) | 调研 ContextualRustAgent 如何基于 Spec、C 源码索引和按需读取机制生成 Rust 项目，而不是单轮整项目生成。 |
| [02_file_planning_and_entry_strategy.md](02_file_planning_and_entry_strategy.md) | 说明 Rust 文件规划、模块布局、入口函数、Cargo 配置和 CLI binary 命名策略如何从 C 项目结构推导。 |
| [03_symbol_registry_and_read_requests.md](03_symbol_registry_and_read_requests.md) | 分析符号注册表和 read request 协议如何支持模型按函数、文件、行范围读取 C/Rust 上下文。 |
| [04_baseline_generators.md](04_baseline_generators.md) | 对比 RustAgent、StableRustAgent、GrowthRustAgent 等生成器路径，作为论文消融或 legacy baseline 的材料。 |
