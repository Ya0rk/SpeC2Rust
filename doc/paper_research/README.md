# 论文调研文档入口

本目录用于组织 C 到 Rust 翻译项目的论文调研材料。调研主线以 `scripts/agent.sh` 当前默认流程为准：

```text
SpecAgent
  -> ContextualRustAgent
  -> UnfinishedCodeAgent
  -> RustRepairAgent
  -> RustTestAgent
```

早期链路 `CDocAgent -> RustAgent -> CodeFixer -> TestFixer` 作为基线和消融对象，不作为论文方法主线。

## 目录说明

| 目录 | 内容 |
| --- | --- |
| `00_index/` | 研究地图、术语表、论点与证据矩阵。 |
| `01_pipeline_orchestration/` | 入口脚本、主流程状态机、复现与日志。 |
| `02_c_program_understanding/` | C 静态分析、模块切分、SpecAgent、pointer/macro 证据。 |
| `03_context_construction/` | 上下文索引、translation contract、prompt 预算、round log。 |
| `04_rust_generation/` | ContextualRustAgent、文件计划、符号注册表、基线生成器。 |
| `05_compile_repair/` | RustRepairAgent、错误前沿、错误分批、确定性结构修复。 |
| `06_functional_testing/` | RustTestAgent、测试 wrapper、snapshot、LogAgent、probe。 |
| `07_metrics_and_evaluation/` | 数据集、成功指标、消融计划、失败分类、成本分析。 |
| `08_engineering_optimizations/` | 环境隔离、上下文去重、输出契约、安全防护。 |
| `09_paper_materials/` | 方法章节提纲、系统图、表格、相关工作映射。 |

## 阅读顺序

建议先读：

1. `00_index/00_research_map.md`
2. `01_pipeline_orchestration/02_main_py_workflow.md`
3. `02_c_program_understanding/03_spec_agent_documents.md`
4. `04_rust_generation/01_contextual_rust_agent.md`
5. `05_compile_repair/01_rust_repair_agent.md`
6. `06_functional_testing/01_rust_test_agent.md`

随后根据论文写作需要补读指标、工程优化和图表材料。

## 写作规范

每份模块调研文档应尽量包含：

- 研究问题
- 流程或数据流
- 关键工程细节
- 可引用代码证据
- 实验钩子
- 局限与反例
- 可写入论文位置

新增论点、证据或风险时，应同步回填到 `00_index/02_claims_and_evidence_matrix.md`。
