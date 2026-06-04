# SpecAgent 文档生成调研

## 研究问题

本节关注 C 项目理解链路的第三层：如何把静态分析和模块切分结果转换成下游 Rust 生成 Agent 可消费的文档体系。

核心研究问题包括：

- 如何把 C 程序事实压缩成「人类可读文档」和「机器可读契约」两类上下文？
- 哪些文档应当确定性生成，哪些文档可以由 LLM 生成？
- 如何通过 prompt、contract、lint 和文档结构约束范围扩张？
- 生成的 `spec.md`、`plan.md`、`tasks.md` 如何服务迁移，而不是变成新产品设计？

## 流程 / 数据流

`scripts/agent.sh` 默认启用 `--use-spec-agent`，可选通过环境变量启用指针和宏专项分析。`SpecAgent.analyze_and_generate_spec()` 是文档生成主入口。

当前流程可以分成三层：

```text
静态分析层
  -> collect_project_info
  -> CCodeAnalyzer.analyze_directory
  -> get_project_analysis
  -> build_dependency_graph
  -> ModuleSplitter.split

认知压缩层
  -> docs/rewrite-context/00_repo_manifest.md
  -> docs/rewrite-context/01_subsystems/*.md
  -> docs/rewrite-context/02_interfaces/*.md
  -> docs/rewrite-context/translation_contract.json
  -> docs/rewrite-context/03_behaviors/001_behavior_specification.md
  -> .specify/memory/constitution.md

执行规划层
  -> specs/<index>-<module>-rust-port/spec.md
  -> specs/<index>-<module>-rust-port/plan.md
  -> specs/<index>-<module>-rust-port/tasks.md
  -> 可选 pointer.md / macro.md
  -> 可选 04_gaps_and_risks/001_pointer_macro_summary.md
  -> translation_lint.json
```

主流程的重要设计是：先生成全局 rewrite context，再为每个 `module_unit` 生成 spec-kit 文档。这样 Rust 生成阶段既能看到项目级事实，也能按模块消费局部迁移计划。

## 关键工程细节

### 确定性文档优先

`00_repo_manifest.md`、`01_subsystems/*.md`、`02_interfaces/*.md` 当前主要由本地模板拼装，不再让 LLM 自由总结。这能减少假头文件、假接口、假错误码等幻觉。

例如接口文档生成会按模块收集：

- 相关头文件。
- 函数事实。
- 结构体定义。
- 引用但未在本模块定义的结构体名。
- 宏和全局变量。

总览页只做索引，详细事实放入模块级接口文档，避免全量重复。

### `translation_contract.json` 作为机器可读边界

`_build_translation_contract()` 生成迁移契约，字段包括：

- 项目名、项目类型、构建系统。
- 源文件清单和文件角色。
- `module_units`。
- `generation_boundary`：允许生成的 Rust 文件、是否允许测试、示例、bench、FFI、依赖策略。
- `forbidden_without_evidence`：无证据禁止项，例如 `serde`、`criterion`、`thread_safe_api`、`ffi`。
- 类型、函数、宏事实。

这是当前文档体系里最接近「唯一事实源」的部分。论文中可以把它解释为文档驱动迁移的范围裁决层。

### LLM 文档的续写和清洗

`_generate_markdown_with_continuation()` 通过 `<CGR_DONE>` 控制多轮续写，避免长文档半途截断。生成后会做轻量清洗：

- 去掉外层 Markdown fence。
- 删除重复标题。
- 删除同一 section 内重复列表项。
- 合并多余空行。

这是一类重要的工程优化：它不改变业务语义，但降低生成文档重复和格式不稳定。

### Prompt 约束范围扩张

`prompt.py` 已经加入多处范围约束：

- 接口文档要求只使用输入中显式提供的头文件、函数、结构体和位置，不得发明签名、宏、错误码或配置项。
- 行为文档要求证据不足时明确标注「当前模块摘要不足」，不得使用 `possibly`、`infer`、`probably`。
- 模块 spec 要求每条需求可追溯到模块文件、函数或类型，不得扩展新 API、线程安全、序列化、恢复机制、FFI 或 benchmark。
- 模块 plan 要求默认使用标准库，不为「优雅」扩展模块或设施。
- 模块 tasks 要求只写可从输入文件推断的 Rust 目标路径，不添加无证据工程任务。

### 文档 lint

`_lint_generated_docs()` 不阻断生成流程，但会扫描越界信号并输出 `translation_lint.json`。当前检查包括：

- Phase 8 / 9 / 10 等过长阶段。
- 发布相关内容，如 `crates.io`、`CHANGELOG`、`PERFORMANCE.md`。
- 无证据高级能力，如线程安全、恢复机制。
- 未授权依赖或测试框架，如 `serde`、`criterion`、`proptest`。
- 未授权 FFI。
- 出现在 contract 允许列表之外的 Rust 文件路径。

这为论文实验提供了一个直接可量化的「文档越界」指标。

## 可引用代码证据

| 证据点 | 代码位置 | 可引用结论 |
| --- | --- | --- |
| 主流程三层注释 | `src/agent/spec_agent.py:2293-2298` | `SpecAgent` 明确把流程分为静态分析、认知压缩、执行规划。 |
| AST 和依赖图生成 | `src/agent/spec_agent.py:2319-2332` | 文档生成基于 `CCodeAnalyzer` 和依赖图，而不是直接让 LLM 阅读项目。 |
| 模块切分决定后续粒度 | `src/agent/spec_agent.py:2334-2341` | `module_units` 决定后续每个模块生成哪些文档。 |
| 确定性 repo manifest | `src/agent/spec_agent.py:1537-1562` | 仓库地图由事实模板生成，避免 LLM 补全目录。 |
| 子系统文档 | `src/agent/spec_agent.py:1564-1623` | 模块摘要由模块事实构造，是行为文档和 spec 的上游。 |
| 接口文档按模块生成 | `src/agent/spec_agent.py:1625-1694` | 接口事实分模块写入，并生成总览索引。 |
| 迁移契约 | `src/agent/spec_agent.py:1089-1207` | contract 聚合文件、模块、边界、禁止项、类型、函数和宏。 |
| contract 写入 | `src/agent/spec_agent.py:1209-1226` | `translation_contract.json` 固化为 rewrite context 的一部分。 |
| 文档 lint | `src/agent/spec_agent.py:1228-1288` | 系统有范围扩张检查，且能输出机器可读发现。 |
| constitution 生成 | `src/agent/spec_agent.py:1796-1844` | 项目级原则文档消费精炼后的接口和行为摘要。 |
| per-module spec / plan / tasks | `src/agent/spec_agent.py:1846-1999` | 每个模块独立生成 spec-kit 执行文档。 |
| 接口 prompt 禁止发明事实 | `src/config/prompt.py:952-957` | prompt 层强调缺失信息必须标注，不得假设。 |
| 行为 prompt 禁止推断 | `src/config/prompt.py:981-985` | 行为文档必须来自输入证据，不得推断。 |
| 模块 spec 范围约束 | `src/config/prompt.py:1367-1376` | 模块 spec 明确禁止重复列事实和扩展能力。 |
| 模块 plan 范围约束 | `src/config/prompt.py:1426-1440` | 模块 plan 限制依赖、模块数量和无证据工程能力。 |
| 模块 tasks 范围约束 | `src/config/prompt.py:1477-1492` | tasks 限制文件路径、测试任务和后期工程阶段。 |

## 实验钩子

建议围绕文档体系设计以下实验：

- **文档重复率**：统计函数签名、类型名、文件路径在多文档中的重复次数，比较优化前后 `c_docs` 总体积和重复率。
- **越界率**：用 `translation_lint.json` 统计每个项目的 `scope_expansion` 和 `out_of_scope_file` 数量。
- **contract 覆盖率**：计算 contract 中函数、类型、宏数量与 AST 分析结果的覆盖比例。
- **LLM 文档消融**：比较「只用 contract + 接口文档」和「全量 Markdown」对 Rust 生成结果的影响。
- **续写稳定性**：记录每份文档实际续写轮数、重复标题数、清洗前后字符数。
- **错误归因实验**：把 Rust 编译错误按来源归因到缺失接口事实、错误行为推断、错误模块边界或 pointer/macro 风险。

可直接产出论文图表：

- 每个阶段的文档数量和平均大小。
- lint 发现数量随 prompt 约束增强的变化。
- contract 外文件路径数量与 Rust 编译失败率的相关性。
- 启用 `translation_contract.json` 前后的生成文件集合差异。

## 局限与反例

- **部分文档仍依赖 LLM**：`03_behaviors`、`constitution.md`、`spec.md`、`plan.md`、`tasks.md` 仍可能产生幻觉，只是通过 prompt 和 lint 降低风险。
- **lint 只报告不阻断**：当前发现越界文档后不会自动拒绝生成，也不会回写修复。
- **prompt 存在语言不一致**：部分全局 prompt 要求简体中文，而模块级 prompt 要求英文标题和正文。这可能影响中文论文材料的直接复用。
- **上下文预算未真正收紧**：`SpecAgent` 里多个 `MAX_*_CHARS` 当前设置为极大值，方法存在但实际约束较弱。
- **per-module 文档可能缺源码体**：spec / plan / tasks 主要消费函数事实行和结构体事实行，不一定包含完整函数体，复杂行为仍依赖前序行为文档。
- **contract 设计仍在演进**：`translation_contract.json` 已有边界字段，但 Rust 端是否强制执行，需要结合后续 Agent 调研确认。
- **文档清洗是格式级**：去重主要处理重复标题和列表项，无法证明语义重复完全消除。

典型反例：

```text
如果模块接口文档写明只有 src/lib.rs 可生成，
但 plan.md 又建议 src/ffi.rs，
当前 lint 会记录 out_of_scope_file，
但不会自动删除 plan.md 中的错误内容。
```

## 可写入论文位置

建议放入论文的「System Architecture」和「Context Construction」章节，标题可为：

- `Documentation-mediated C-to-Rust Translation`
- `Migration Contract and Scope Control`
- `Spec-driven Agentic Translation Pipeline`

可强调的技术贡献：

- 将 C 项目理解结果组织为多层 rewrite context，而不是单次 prompt。
- 用 `translation_contract.json` 把迁移范围、允许文件和禁止能力结构化，作为下游生成边界。
- 结合确定性事实文档、LLM 规划文档和文档 lint，形成可追溯、可消融、可度量的工程化上下文体系。

