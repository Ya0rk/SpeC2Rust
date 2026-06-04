# TLDR

本目录覆盖 rtest 功能测试修复阶段：如何用原始 C shell 测试验证 Rust CLI，并在失败时执行证据驱动修复。

| 文件 | 讲什么 |
| --- | --- |
| [01_rust_test_agent.md](01_rust_test_agent.md) | 总览 RustTestAgent 的功能测试修复闭环：构建 C reference、复制只读测试、首跑失败、注入材料、LLM 编辑和回归检查。 |
| [02_test_runner_and_wrappers.md](02_test_runner_and_wrappers.md) | 分析 TestRunner 和 wrapper 执行环境，说明 Rust/C binary 如何 stage、如何通过 bash function 映射命令、为何不污染 `PATH`。 |
| [03_snapshot_regression_policy.md](03_snapshot_regression_policy.md) | 说明项目快照、回滚和 baseline 回归保护机制，确保当前用例通过不会破坏已有通过用例。 |
| [04_log_agent_runtime_evidence.md](04_log_agent_runtime_evidence.md) | 调研 LogAgent 如何压缩测试运行时证据、保存 runtime log，并把运行产物注入后续修复 prompt。 |
| [05_dynamic_and_static_probes.md](05_dynamic_and_static_probes.md) | 说明 debug probe 和 static probe 的取证流程、互斥规则、证据文件和 C/Rust 双侧行为对照。 |
| [06_rtest_step_by_step_scenario.md](06_rtest_step_by_step_scenario.md) | 以 `which` 项目为例，用 101 个编号步骤展开 rtest 的真实执行过程、材料注入、LLM 修复、构建验证和异常分支。 |
