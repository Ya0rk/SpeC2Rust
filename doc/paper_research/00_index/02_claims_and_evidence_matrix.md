# 论点与证据矩阵

## 使用方式

本文件用于把论文论点、代码证据和实验验证方式绑定起来。各子调研文档完成后，应把可引用机制回填到本表。

## 论点矩阵

| 论文论点 | 代码证据 | 实验钩子 | 局限与反例 | 状态 |
| --- | --- | --- | --- | --- |
| 系统是闭环迁移框架，而不是单次 LLM 翻译 | `scripts/agent.sh` 默认启用 Spec、ContextualRust、RustRepair、RustTest；`main.py` 串联预检、生成、修复、测试 | 对比默认链路与关闭修复/测试链路的成功率 | 若 C 项目不可构建，闭环 oracle 不成立 | B：见 `01_pipeline_orchestration/`、`07_metrics_and_evaluation/03_ablation_plan.md` |
| C 项目理解通过外部静态分析降低模型负担 | `CCodeAnalyzer`、`SpecAgent`、`ModuleSplitter` | 对比 `SpecAgent` 与 `CDocAgent`；统计文档长度与成功率 | 模块切分依赖调用图质量 | B：见 `02_c_program_understanding/` |
| 按需上下文优于全量文档注入 | `ContextualRustAgent`、`ContextualSpecAgent`、symbol registry、read request | 对比 `ContextualRustAgent` 与 `RustAgent` 请求数、成功率、生成文件漂移 | 若索引召回不足，可能漏关键事实 | B：见 `03_context_construction/`、`04_rust_generation/` |
| 编译前沿机制降低越修越坏风险 | `RustRepairAgent._should_accept_result`、`error_signature`、`frontier_metrics` | 统计 accepted frontier 次数、回退次数、最终编译通过率 | 错误签名变化不一定代表真实进展 | B：见 `05_compile_repair/` |
| 功能测试阶段以 C 行为作为 oracle | `RustTestAgent`、`TestRunner`、C binary/Rust binary wrapper | 统计 shell 测试通过率、按用例修复轮数 | 依赖 C 项目可 clean/build，且测试脚本可迁移 | B：见 `06_functional_testing/`、`07_metrics_and_evaluation/02_success_metrics.md` |
| 运行时证据让 LLM 可主动诊断 | `LogAgent`、`RuntimeProbeService`、`debug_probe`、`static_probe_update` | 开启/关闭 `--use-log-agent` 的修复成功率与轮数 | LLDB 环境、断点行号和表达式合法性限制 | B：见 `06_functional_testing/04_log_agent_runtime_evidence.md`、`06_functional_testing/05_dynamic_and_static_probes.md` |
| 工程防护保证实验复现和安全 | Python 环境选择、只读测试、snapshot、禁止 fake 实现、日志记录 | 检查日志完整性、测试目录是否被修改、snapshot 回滚记录 | 仍可能有平台差异和 shell 兼容问题 | B：见 `08_engineering_optimizations/` |

## 证据等级

| 等级 | 定义 |
| --- | --- |
| A | 有代码路径、日志产物和实验数据同时支撑。 |
| B | 有代码路径和日志/产物支撑，但缺少系统实验。 |
| C | 仅为实现意图或文档描述，需要进一步验证。 |

## 当前待补证点

- 默认链路下每阶段 LLM 请求数和耗时：见 `07_metrics_and_evaluation/05_cost_and_latency.md`，需要补阶段级抽取脚本。
- `SpecAgent` 上下文预算常量当前是否实际生效：见 `02_c_program_understanding/03_spec_agent_documents.md`。
- `translation_contract` 在代码中的真实实现位置与使用路径：见 `03_context_construction/02_translation_contract.md`。
- `ContextualRustAgent` 的 read request 是否在实际 round logs 中频繁出现：见 `04_rust_generation/03_symbol_registry_and_read_requests.md`，需要从 `log/round_logs/` 统计。
- RustRepairAgent 的 “frontier accepted” 记录能否从 `repair_journal.jsonl` 自动统计：见 `05_compile_repair/02_error_frontier_and_acceptance.md`。
- RustTestAgent 的回归保护、fake 实现检测和 probe 使用频率能否量化：见 `06_functional_testing/03_snapshot_regression_policy.md`、`06_functional_testing/05_dynamic_and_static_probes.md`。
