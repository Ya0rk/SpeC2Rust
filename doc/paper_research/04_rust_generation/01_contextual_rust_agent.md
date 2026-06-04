# ContextualRustAgent：按需上下文 Rust 生成

## 研究问题

本节研究 `ContextualRustAgent` 如何在 C 到 Rust 项目翻译中替代传统全文 prompt 生成方式。核心问题是：模型是否可以在较小、聚焦的上下文中逐文件生成 Rust，同时通过符号表、迁移契约和本地 lint 抑制重复定义、越界功能和 C ABI 泄漏。

## 流程 / 数据流

`ContextualRustAgent.generate_code()` 的主要流程如下：

1. 构建 `SpecDocumentIndex` 和 `ContextualSpecAgent` 的 Rust generation context。
2. 根据 `translation_contract` 和源码事实构建模块索引。
3. 程序化推导初始文件计划，必要时通过 LLM 输出 `<CGR_PLAN>`。
4. 生成项目结构设计。
5. 生成实现计划，并允许实现计划用 `<new_files_to_generate>` 重排已有文件。
6. 初始化 `.cgr_generation_plan.json` 和 `.cgr_api_contract.json`。
7. 逐文件生成：本地生成 `Cargo.toml` 和 `src/lib.rs`，其他文件由 LLM 生成。
8. 每个 `.rs` 文件写入前执行 contract lint、Rust 风格 lint、重复定义检查和引用检查。
9. 对违规文件先局部修复，再整文件修复，仍违规时要求模型给出 force-write 决策。
10. 写入后更新 registry 和 API contract。

## 关键工程细节

`ContextualRustAgent` 的关键差异是「静态 prompt 放索引，不放全文」。类注释明确说明：文件 prompt 只接收相关 spec/source snippets 和 symbol table，模型可以用 `<CGR_READ>` 请求更多材料，已生成文件用 registry 表达而不是全文拼接。

文件级 prompt 来自 `RustGenerationSpecPrompts.file_generation_prompt()`，包含：

- 目标文件路径、职责、`owns`、对应 C source files / functions、依赖文件。
- 当前文件相关的 project plan summary。
- 已生成 Rust symbol table。
- 当前文件相关的 spec context。
- 关键 C source 内联片段和其余 C source index。
- Rust rewrite contract。
- 禁止 raw pointer、`unsafe`、`c_void`、`repr(C)`、`extern "C"` 和 C 风格函数名。

生成过程使用两个本地兜底：

- `Cargo.toml` 由 `_build_fallback_cargo_toml()` 直接生成，避免模型输出混入 Rust 代码或不合法 TOML。
- `src/lib.rs` 根据 registry 中实际已生成模块重建，避免模型把未生成模块错误 re-export。

修复策略也分层：

- 局部修复要求模型返回 JSON edits，带真实行号，只允许 replace / delete / insert。
- 如果局部修复失败，再要求整文件修复。
- 如果仍存在 fatal finding，调用 force-write prompt，让模型明确说明是否必须写入。

## 可引用代码证据

- `src/agent/alternatives/contextual_rust_agent.py:935-943`：类注释定义 contextual 路径与 baseline 的差异。
- `src/agent/alternatives/contextual_rust_agent.py:1155-1179`：静态项目上下文只放契约、入口策略、源码接口摘要和文档索引。
- `src/agent/alternatives/contextual_rust_agent.py:1961-1973`：构建单文件 prompt，注入目标 plan、registry summary、spec context 和 source context。
- `src/agent/alternatives/contextual_rust_agent.py:1975-2031`：文件生成前先处理 `<CGR_READ>`，再用 `<CGR_DONE>` 做 continuation。
- `src/agent/alternatives/contextual_rust_agent.py:2033-2116`：单文件生成、lint、repair、依赖检测、写入和 contract 更新。
- `src/agent/alternatives/contextual_rust_agent.py:2118-2131`：合并 contract lint、C ABI leak lint、重复定义和引用检查。
- `src/agent/alternatives/contextual_rust_agent.py:2227-2355`：局部修复优先，失败后回退整文件修复。
- `src/agent/alternatives/contextual_rust_agent.py:2537-2596`：根据 registry 重建 `src/lib.rs` 和 re-export。
- `src/agent/alternatives/contextual_rust_agent.py:2653-2709`：`generate_code()` 的主流程。
- `src/agent/alternatives/rust_generation_spec_agent.py:327-396`：单文件生成 prompt 的系统约束和读取协议。

## 实验钩子

- **与默认 RustAgent 对比：** 统计生成阶段 prompt tokens、LLM 请求轮数、编译成功率、重复定义数量和测试通过率。
- **registry 消融：** 禁用 `_build_targeted_registry_summary()`，观察重复 struct / free function、错误 private field 访问和 unplanned module 引用。
- **local lib.rs 消融：** 改为 LLM 生成 `src/lib.rs`，比较未生成模块引用和 re-export 错误。
- **局部修复收益：** 统计局部修复成功率、整文件修复回退次数、force-write 次数。
- **C ABI 泄漏检查：** 统计 raw pointer、`unsafe`、`*_t`、`*_free` 等 pattern 在 lint 前后的数量。

## 局限与反例

- registry 和 lint 均为启发式正则实现，不是完整 Rust AST 或 borrow checker 级别分析。
- 生成顺序依赖计划中的 `depends_on`，如果依赖缺失，模型仍可能引用未生成模块。
- 本地生成 `src/lib.rs` 偏向公开 re-export，可能暴露本应私有的类型。
- force-write 是安全阀，但也可能让仍有违规的文件进入后续编译修复阶段。
- `ContextualRustAgent` 解决的是初始生成上下文控制，不能替代后续 cargo check、repair 和功能测试。

## 可写入论文位置

建议作为「方法」章节的核心生成模块，标题可为「Demand-driven Rust Generation with Symbol Guardrails」。这是 A2 中最适合作为主要技术贡献展开的部分。

