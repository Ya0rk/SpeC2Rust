# Spec 上下文索引与按需检索

## 研究问题

本节关注的问题是：如何把 SpecAgent 产出的长篇 Markdown、源码 JSON 和迁移契约压缩成 Rust 生成阶段可消费的上下文，而不是把所有文档全文塞进单轮 prompt。

核心假设是：Rust 文件生成需要的是「当前目标文件相关的行为、接口、源码和模块边界」，而不是完整文档全集。工程实现上，项目采用两级上下文索引：

- `SpecDocumentIndex`：`ContextualRustAgent` 内部的轻量回退索引，按文档路径、标题、模块名和 kind 做粗粒度选择。
- `RustGenerationSpecAgent`：当前 contextual 路径的主要只读索引，把文档拆成 section，并融合 C 源码记录和 `translation_contract.json`。

## 流程 / 数据流

默认 `scripts/agent.sh` 会开启 `--use-spec-agent` 和 `--use-contextual-rust-agent`，对应主流程中的 Spec 文档生成与上下文式 Rust 生成。`main.py` 在步骤 1 收集 `c_docs/.specify/memory`、`c_docs/docs/rewrite-context`、`c_docs/specs`，随后在步骤 2 调用 `ContextualRustAgent.generate_from_docs(...)`。

上下文构造的数据流如下：

1. SpecAgent 生成 `docs/rewrite-context`、`.specify/memory`、`specs/*` 和 `translation_contract.json`。
2. `main.py` 把文档目录和 `src/parse/res/<project>.json` 传给 Rust agent。
3. `RustAgent.configure_source_context()` 加载 C 函数级记录，形成 `source_records`。
4. `ContextualRustAgent.generate_code()` 构建 `SpecDocumentIndex`，同时创建 `ContextualSpecAgent(enable_c_pipeline=False)`。
5. `ContextualSpecAgent.load_rust_generation_context()` 创建 `RustGenerationSpecAgent`，提供 `build_file_plan()`、`context_for_file()`、`context_for_query()` 和 `overview()`。
6. 每个 Rust 文件生成前，`ContextualRustAgent._build_file_prompt()` 只拉取当前文件相关的 spec section、C 源码片段和已生成 Rust 符号表。

## 关键工程细节

`SpecDocumentIndex` 把每个文档记录为 `DocumentSlice`，字段包括 `rel_path`、`title`、`kind`、`module` 和 tokens。它会跳过 `translation_lint.json`，根据路径判断文档类型，例如 `00_repo_manifest.md`、`01_subsystems`、`02_interfaces`、`03_behaviors`、`04_gaps_and_risks`、`specs/*/spec.md`、`plan.md` 和 `tasks.md`。

主索引 `RustGenerationSpecAgent` 的粒度更细。它把文档按 1 到 4 级标题切成 `DocSection`，从正文中抽取 `.c` / `.h` 文件路径、反引号中的符号、`*_t` 类型名、疑似函数调用，以及接口文档中的函数签名。随后它将这些信息与 `source_records` 和 `translation_contract.json` 融合，建立：

- `source_files`：所有可映射到 Rust 文件的 C / H 文件。
- `function_to_source`：C 函数到源文件的映射。
- `source_to_functions`：源文件到函数集合的映射。
- `source_to_types`：源文件到 C 类型集合的映射。
- `function_signatures`：接口文档中可直接引用的函数签名。

检索不是简单 BM25，而是结合多个工程信号：

- 路径、标题、源码文件、符号、计划拥有的 Rust symbol 共同构成 query。
- source overlap 权重最高，避免把其他 C 文件的行为误带入当前 Rust 文件。
- interface、behavior、module-spec 有额外权重，module-plan 和 pointer/macro 文档权重较低。
- 如果 `plan.source_files` 明确存在，索引会进行严格模块过滤，不让无关 source stem 的 section 混入。
- section 内部再拆 semantic block，按 anchor 命中和 soft budget 选择局部段落。

这套设计的论文价值在于：上下文压缩不是单纯摘要，而是「文件计划驱动的证据路由」。

## 可引用代码证据

- `scripts/agent.sh:160-169`：默认开启 `--use-contextual-rust-agent`、`--use-spec-agent`，并设置 RustTestAgent prompt budget。
- `src/agent/main.py:573-592`：SpecAgent 路径下收集 `.specify/memory`、`docs/rewrite-context` 和 `specs`。
- `src/agent/main.py:629-650`：创建 `ContextualRustAgent`，传入文档路径、C 项目路径和源码 JSON。
- `src/agent/alternatives/contextual_rust_agent.py:89-119`：`SpecDocumentIndex` 建立轻量文档切片。
- `src/agent/alternatives/contextual_rust_agent.py:220-323`：按文档 kind、模块、query token 选择和格式化 spec slice。
- `src/agent/alternatives/contextual_rust_agent.py:2653-2661`：`ContextualRustAgent.generate_code()` 初始化 `SpecDocumentIndex` 和 `ContextualSpecAgent` 的 Rust 上下文视图。
- `src/agent/alternatives/contextual_spec_agent.py:2428-2484`：`ContextualSpecAgent` 作为 Rust 生成上下文门面。
- `src/agent/alternatives/rust_generation_spec_agent.py:533-551`：主索引初始化 `sections`、`source_files`、`function_to_source`、`source_to_functions` 等字段。
- `src/agent/alternatives/rust_generation_spec_agent.py:567-601`：构建 section 索引，并融合 `source_records` 和 translation contract。
- `src/agent/alternatives/rust_generation_spec_agent.py:690-758`：按标题拆文档、抽取源码文件、符号和函数签名。
- `src/agent/alternatives/rust_generation_spec_agent.py:1111-1203`：按文件计划对 section 排名，并严格过滤无关模块。
- `src/agent/alternatives/rust_generation_spec_agent.py:1203-1467`：按 semantic block 和 soft budget 截取局部证据。

## 实验钩子

- **全文上下文 vs. 索引上下文：** 对同一项目分别使用默认 `RustAgent` 和 `ContextualRustAgent`，比较 LLM 请求轮数、prompt tokens、编译成功率和测试通过率。
- **检索精度：** 记录每个 `RustFilePlan` 选中的 top 3 section，人工标注是否与目标文件的 C source overlap、source function 和 target symbol 对齐。
- **消融 strict source filtering：** 去掉 `plan.source_files` 过滤，观察重复定义、错误模块职责和无关能力扩写数量。
- **soft budget 变体：** 调整 `_focused_section_excerpt()` 的 `soft_max_chars`，观察生成质量与 prompt 长度的关系。
- **索引来源消融：** 分别关闭 `source_records`、`translation_contract`、Markdown section，评估文件计划和生成正确性的下降。

## 局限与反例

- `RustGenerationSpecAgent._clip()` 当前直接返回全文，未实际裁剪，说明上下文长度控制主要依赖 section 选择，而不是全局 hard cap。
- section 和 symbol 抽取依赖正则，不是 Markdown AST 或 C AST，遇到复杂代码块、表格或非规范标题时可能漏检。
- source overlap 的严格过滤可能误伤跨文件行为，例如一个 C 文件只负责调度，但关键语义散落在多个 helper 文件。
- 对 `pointer.md` 和 `macro.md` 的使用是可选证据，若开关未启用，旧文档会被过滤，可能导致某些指针 / 宏迁移提示缺失。
- `function_to_source` 来自源码 JSON 和契约，若前置 C 分析漏掉函数，后续上下文检索无法补回。

## 可写入论文位置

建议放入「方法」章节的上下文构造小节，标题可为「File-plan-driven Evidence Retrieval」。可以作为系统贡献之一：将 C 项目翻译任务从全文 prompt 转换为按目标 Rust 文件检索的证据路由问题。

