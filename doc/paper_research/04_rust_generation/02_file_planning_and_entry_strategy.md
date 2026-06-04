# 文件规划与 Rust 入口策略

## 研究问题

本节研究系统如何把 C 项目映射为 Rust 文件集合，并决定生成可执行 crate 还是 library crate。文件规划直接影响翻译质量：规划过大容易诱导模型扩写，规划过小会漏掉 C 行为；入口策略错误则会导致 `src/main.rs` / `src/lib.rs` 相互误用。

## 流程 / 数据流

默认脚本把 `--rust-entry-kind main` 传给主程序，因此 contextual 路径默认强制生成可执行入口。`main.py` 也允许通过 `--rust-entry-kind auto|main|lib` 覆盖。

`ContextualRustAgent` 的文件规划流程：

1. 从 `translation_contract.generation_boundary.allowed_rust_files` 取得候选文件；若没有契约，则从 spec/source 事实推导。
2. 应用入口策略，过滤掉不允许的 `src/main.rs` 或 `src/lib.rs`。
3. 确保 `Cargo.toml`、入口文件和 `README.md` 存在。
4. 通过 `RustGenerationSpecAgent.build_file_plan()` 生成 `RustFilePlan`，包含 `path`、`role`、`owns`、`depends_on`、`spec_queries`、`source_files`、`source_functions`。
5. 按文件类型、基础类型优先级和依赖关系排序。
6. LLM 实现计划只能重排已有文件，不能添加新文件。

## 关键工程细节

入口策略的优先级如下：

- 用户显式指定 `main` 或 `lib` 时，直接使用。
- 否则读取 `translation_contract.project.kind`，CLI / mixed / executable / binary 推导为 `main`，library / lib 推导为 `lib`。
- 如果契约没有项目类型，则检测生产源码中是否存在非测试 `main` 函数，有则 `main`，否则 `lib`。

入口文件过滤是硬规则：

- main 模式下禁止规划、生成或依赖 `src/lib.rs`。
- lib 模式下禁止规划、生成或依赖 `src/main.rs`。
- 无论模型计划如何，都补齐 `Cargo.toml`、入口文件和 `README.md`。

程序化文件计划的特点：

- C source `foo.c` 默认映射到 `src/foo.rs`。
- `main.c` 默认映射到 `src/main.rs`。
- tests / benches 源文件只有在允许测试 / benchmark 时才应进入边界。
- target symbol 不直接复制 C 函数名，而是将 `foo_new`、`foo_free` 等映射成 `Foo::new`、`Drop for Foo` 等 Rust 风格目标。
- `src/lib.rs` 的依赖是所有非入口 `src/*.rs`，并在 contextual 路径中本地重建。

## 可引用代码证据

- `scripts/agent.sh:160-169`：默认开启 contextual、Spec、repair、test agent，并设置 `--rust-entry-kind main`。
- `src/agent/main.py:320-328`：CLI 参数定义 `--use-contextual-rust-agent` 和 `--rust-entry-kind auto|main|lib`。
- `src/agent/alternatives/contextual_rust_agent.py:1029-1077`：入口策略推导，包含用户选择、contract project kind 和生产 `main` 函数检测。
- `src/agent/alternatives/contextual_rust_agent.py:1078-1093`：把入口策略写入 prompt 上下文。
- `src/agent/alternatives/contextual_rust_agent.py:1095-1118`：过滤 forbidden entry，并补齐 `Cargo.toml`、入口文件和 `README.md`。
- `src/agent/alternatives/contextual_rust_agent.py:1395-1414`：有 Rust context 时程序化构建计划；无 context 时通过 LLM `<CGR_PLAN>` 规划。
- `src/agent/alternatives/contextual_rust_agent.py:1427-1444`：fallback 文件列表来自 contract、spec 推断或 source records。
- `src/agent/alternatives/contextual_rust_agent.py:1446-1523`：规范化计划 payload，应用入口策略并更新 `_plan_by_path`。
- `src/agent/alternatives/contextual_rust_agent.py:1546-1586`：按基础类型、入口和依赖关系排序文件。
- `src/agent/alternatives/contextual_rust_agent.py:1628-1661`：实现计划可用 `<new_files_to_generate>` 重排文件顺序。
- `src/agent/alternatives/rust_generation_spec_agent.py:776-824`：根据 source files 构建 `RustFilePlan`。
- `src/agent/alternatives/rust_generation_spec_agent.py:826-924`：把 C 函数名转换为 Rust-style target symbols。
- `src/agent/alternatives/rust_generation_spec_agent.py:960-1021`：根据调用关系和文件优先级排序计划。

## 实验钩子

- **入口策略准确率：** 对 CLI、library、mixed 项目标注真实入口，评估 auto 推导准确率。
- **allowed file 边界：** 比较 contract 文件集合与实际落盘文件集合，统计多生成、少生成和被过滤入口文件。
- **目标 symbol 映射质量：** 抽样 `owns` 中的 `Foo::new`、`Drop for Foo`、`is_empty` 等，评估是否比 C 函数名复制更符合 Rust API。
- **重排收益：** 比较实现计划重排前后的编译错误数量，尤其是类型先生成和依赖先生成的收益。
- **main/lib 消融：** 同一 CLI 项目分别强制 `main`、`lib`、`auto`，比较 cargo check 与功能测试结果。

## 局限与反例

- 默认脚本强制 `--rust-entry-kind main`，对纯 library 项目可能不是最优，需要实验中明确记录。
- source stem 到 Rust 文件的映射对复杂目录结构、多个 C 文件合并模块不够灵活。
- target symbol 映射是启发式，`foo_create` 未必总是 `Foo::new`，也可能是 builder 或 factory。
- 如果前置契约误判 project kind，入口策略会把错误放大到整个生成流程。
- LLM 实现计划只能重排不能增删文件，能抑制扩写，但也限制了合理拆分。

## 可写入论文位置

建议放入「方法」章节的文件计划小节。可以强调系统采用 contract-first 的文件边界，并通过入口策略消除 `main.rs` / `lib.rs` 混用这一类常见 LLM 生成错误。

