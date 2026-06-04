# TLDR

本目录解释端到端翻译流水线如何启动、编排和记录，是理解系统控制流的入口。

| 文件 | 讲什么 |
| --- | --- |
| [01_agent_sh_entry.md](01_agent_sh_entry.md) | 分析 `scripts/agent.sh`、`scripts/rtest_agent.sh` 和 `scripts/run_repair.sh` 的入口职责、默认开关、旁路流程和实验钩子。 |
| [02_main_py_workflow.md](02_main_py_workflow.md) | 梳理 `src/agent/main.py` 的主工作流：从 C 项目输入、文档生成、Rust 生成、编译修复到功能测试阶段的数据流。 |
| [03_reproducibility_and_logging.md](03_reproducibility_and_logging.md) | 总结可复现性和日志体系，包括配置来源、运行目录、阶段日志、指标沉淀方式和后续实验统计入口。 |
