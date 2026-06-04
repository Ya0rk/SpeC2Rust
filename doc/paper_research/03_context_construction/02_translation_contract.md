# Translation Contract：迁移范围契约

## 研究问题

本节研究 `translation_contract.json` 如何把「C 项目事实」转化为 Rust 生成阶段的硬边界。需要解决的问题不是让模型知道更多，而是让模型知道哪些内容不能生成：不能越界规划文件、不能无证据添加依赖、不能自动扩展线程安全 API、恢复机制、发布流程或 FFI 层。

## 流程 / 数据流

契约生成与消费分成两个阶段：

1. `ContextualSpecAgent` 在 C 分析结束后，根据 `project_info`、`project_analysis` 和 `module_units` 构建 `translation_contract.json`。
2. 契约写入 `output/<project>/c_docs/docs/rewrite-context/translation_contract.json`。
3. Rust 生成阶段加载文档目录时，`RustAgent.load_documents()` 会自动查找并加载该文件。
4. `RustAgent._apply_translation_contract()` 将契约中的 `allowed_rust_files`、依赖策略和允许依赖写入 agent 内部状态。
5. `ContextualRustAgent` 把契约摘要放进静态项目上下文，并在每个文件写入前执行 scope lint。

## 关键工程细节

契约包含几类关键字段：

- `project`：项目名称、项目类型和构建系统。
- `files`：C / H / other 文件列表及文件角色。
- `module_units`：模块名、类别和对应 C 文件。
- `generation_boundary`：允许生成的 Rust 文件、是否允许 tests / examples / benches / FFI、依赖策略和允许依赖。
- `forbidden_without_evidence`：无证据禁止扩展的能力，例如 `serde`、`thread_safe_api`、`recovery_mechanism`、`ffi`、`range_query`。
- `types` / `functions` / `macros`：机器可读的 C 类型、函数、宏事实，包括源文件位置、签名和角色。

文件边界推导是项目无关的。`_derive_allowed_rust_files()` 根据项目类型先加入 `Cargo.toml`、`src/main.rs` 或 `src/lib.rs`，再遍历 source 角色的 C 文件，以 stem 推导 `src/<stem>.rs`，最后加入 `README.md`。

函数角色由上下文决定，例如 header 中声明的是 `public_api`，生产 `main` 是 `entrypoint`，测试文件中的函数会被标为 `test_case`、`test_helper` 等。这个分类会影响后续生成范围：测试文件默认不会进入 `allowed_rust_files`，除非配置显式允许。

契约不仅进入 prompt，还进入本地 lint：

- 默认 `std_only_by_default` 且无 `allowed_dependencies` 时，`serde`、`tokio`、`clap`、`rand` 等外部依赖引用会被拦截。
- `allow_ffi=false` 时，`extern "C"`、`#[no_mangle]`、`libc::` 等会被拦截。
- `forbidden_without_evidence` 中的线程安全和恢复机制会触发额外正则检查。
- `ContextualRustAgent` 又增加了 Rust 风格检查，禁止 raw pointer、`unsafe`、`c_void`、`repr(C)`、`NonNull`、C 风格生命周期函数和 `*_t` 类型泄漏。

## 可引用代码证据

- `src/agent/alternatives/contextual_spec_agent.py:1080-1103`：从 C 文件角色和项目类型推导 `allowed_rust_files`。
- `src/agent/alternatives/contextual_spec_agent.py:1105-1222`：构建 `translation_contract.json` 的主体，包括 project、files、module_units、generation_boundary、types、functions、macros。
- `src/agent/alternatives/contextual_spec_agent.py:1225-1242`：写入 `docs/rewrite-context/translation_contract.json`。
- `src/agent/alternatives/contextual_spec_agent.py:1244-1308`：对生成文档做范围 lint，发现 contract 外路径、未授权依赖和未授权 FFI。
- `src/agent/rust_agent.py:315-330`：加载契约后应用 `allowed_rust_files`、`dependency_policy` 和 `allowed_dependencies`。
- `src/agent/rust_agent.py:363-410`：构造迁移契约上下文和硬边界说明。
- `src/agent/rust_agent.py:1352-1385`：写入 Rust 文件前检查未授权依赖和 FFI。
- `src/agent/alternatives/contextual_rust_agent.py:1120-1170`：`ContextualRustAgent` 将契约摘要并入静态项目上下文。
- `src/agent/alternatives/contextual_rust_agent.py:2133-2225`：拦截 C ABI 泄漏、raw pointer、`unsafe`、C 风格命名等。

## 实验钩子

- **契约开关消融：** 有 / 无 `translation_contract.json`，比较越界文件数量、未授权依赖数量、FFI 泄漏数量。
- **allowed file 精度：** 统计 `allowed_rust_files` 与最终生成文件集合的交并比，并记录被拦截的 out-of-scope 计划。
- **project kind 影响：** 对 CLI、library、mixed 项目分别评估入口文件推导是否正确。
- **契约 lint 命中率：** 记录 contract lint 命中的 pattern、文件、是否经 repair 后消失。
- **误报分析：** 人工复核 `thread_safe_api`、`recovery_mechanism`、第三方依赖禁用是否误拦真实 C 语义。

## 局限与反例

- 契约依赖前置 C 分析质量；若 C 函数、类型或 header 声明被漏提取，契约会错误收缩生成边界。
- 默认 `std_only_by_default` 偏保守，可能对依赖外部 crate 才能自然表达的项目产生过强限制。
- scope lint 以正则为主，不能完全理解 Rust AST，复杂路径导入、宏展开或间接依赖可能漏检。
- `allowed_rust_files` 按 C 文件 stem 推导，遇到多个 C 文件自然合并成一个 Rust 模块时可能过度拆分。
- `forbidden_without_evidence` 是人工列出的能力黑名单，对新型越界能力没有自动覆盖。

## 可写入论文位置

建议写入「方法」章节的「Scope Contract」或「Translation Boundary」小节。它可以作为论文中防止 LLM 过度工程化、过度泛化和 C ABI 机械复制的核心机制。

