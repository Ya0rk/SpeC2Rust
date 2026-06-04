# Rust 生成器对照：RustAgent、StableRustAgent、GrowthRustAgent

## 研究问题

本节梳理 contextual 方案之外的三个 Rust 生成器，作为论文实验和消融对照：

- `RustAgent`：默认全功能生成器。
- `StableRustAgent`：更薄、更受控的生成器。
- `GrowthRustAgent`：最小可编译集优先的生长式生成器。

研究问题是：不同生成策略如何影响上下文长度、文件完整性、编译成功率、越界扩写和后续修复成本。

## 流程 / 数据流

`main.py` 根据互斥开关选择 Rust 生成器：

- `--use-contextual-rust-agent` → `ContextualRustAgent`
- `--use-growth-rust-agent` → `GrowthRustAgent`
- `--use-stable-rust-agent` → `StableRustAgent`
- 都不启用 → `RustAgent`

`scripts/agent.sh` 默认选择 contextual，因此 baseline 需要通过修改运行参数或设置 `CGR_NO_DEFAULT_FLAGS=1` 后手动开启。

## 关键工程细节

### RustAgent

默认 `RustAgent` 是功能最完整的 baseline。它的流程是：

1. 生成项目结构。
2. 从 `<project_file>` 树中解析文件列表。
3. 生成实现计划。
4. 从 `<new_files_to_generate>` 中提取重排顺序。
5. 合并项目结构文件、实现计划文件和 contract planned files，避免遗漏。
6. 初始化 `.cgr_generation_plan.json` 和 `.cgr_api_contract.json`。
7. 逐文件生成，并把已生成文件全文追加进后续 context。
8. 写入前执行 contract lint，失败时尝试重新生成。
9. 最终检查缺失文件，严格模式下缺失即报错。

它还支持 `skeleton_first`：先生成骨架，再基于骨架补全实现。优势是结构稳定，缺点是多一轮 LLM 调用，并可能保留不完整骨架。

### StableRustAgent

`StableRustAgent` 目标是更薄、更可控：

- 文档加载时按类型裁剪。
- 先让模型只输出 JSON 文件数组。
- 本地生成最小 `Cargo.toml`。
- 不让模型输出 `lib.rs`，而是在本地重建。
- 对已生成上下文只保留每个文件前 5000 字符。

这条路径适合作为「少机制、低复杂度」baseline。它比默认 `RustAgent` 少很多 contract / source / registry 逻辑，但也缺少 contextual 的按需读取与符号约束。

### GrowthRustAgent

`GrowthRustAgent` 继承 `RustAgent`，额外引入生长式计划：

- 先让模型规划 `trunk_files` 和 `branch_files`。
- 优先生成最小可编译主树干。
- 每写入主树干文件后执行 `cargo check`。
- branch 文件每生成两个检查一次。
- 允许 trunk 阶段对 branch 依赖使用 `todo!()`、`unimplemented!()` 或 placeholder error。

这条路径的优势是早期编译反馈更密集，缺点是 placeholder 会和后续「无假实现」测试修复策略冲突，且主树干选择如果错误，会把后续生成带偏。

## 可引用代码证据

- `src/agent/main.py:481-492`：Rust agent 开关互斥检查。
- `src/agent/main.py:629-641`：根据开关选择 `ContextualRustAgent`、`GrowthRustAgent`、`StableRustAgent` 或默认 `RustAgent`。
- `src/agent/rust_agent.py:1178-1240`：默认生成器的 continuation 机制。
- `src/agent/rust_agent.py:786-843`：`.cgr_generation_plan.json` 初始化和状态更新。
- `src/agent/rust_agent.py:665-710`：`.cgr_api_contract.json` 更新和上下文摘要。
- `src/agent/rust_agent.py:1352-1385`：默认生成器的 contract lint。
- `src/agent/rust_agent.py:2520-2547`：依赖检测受 contract dependency policy 约束。
- `src/agent/rust_agent.py:2575-2664`：单文件生成、骨架回退、截断检查、contract 修复和写入。
- `src/agent/rust_agent.py:2666-2793`：默认 `generate_code()` 主流程和缺失文件补生成。
- `src/agent/alternatives/stable_rust_agent.py:18-26`：Stable 设计原则。
- `src/agent/alternatives/stable_rust_agent.py:50-102`：Stable 文档裁剪与上下文收集。
- `src/agent/alternatives/stable_rust_agent.py:436-465`：Stable 只要求模型输出 JSON 文件数组。
- `src/agent/alternatives/stable_rust_agent.py:658-690`：Stable 本地生成 Cargo、逐文件生成并本地重建 lib。
- `src/agent/alternatives/growth_rust_agent.py:8-17`：Growth 设计思路。
- `src/agent/alternatives/growth_rust_agent.py:19-69`：生成 trunk / branch 生长计划。
- `src/agent/alternatives/growth_rust_agent.py:105-158`：生成 trunk / branch 文件时允许 placeholder。
- `src/agent/alternatives/growth_rust_agent.py:164-227`：Growth 主流程和 cargo check 节奏。

## 实验钩子

- **四路生成器对比：** `RustAgent`、`StableRustAgent`、`GrowthRustAgent`、`ContextualRustAgent` 使用同一 C 项目、同一模型、同一 repair/test 配置。
- **上下文增长曲线：** 统计每个生成器的 round log Request Tokens，观察默认 `RustAgent` 的 generated context 是否随文件数线性增长。
- **文件完整性：** 统计缺失文件补生成次数、strict full generation 失败次数。
- **越界扩写：** 统计 contract lint、C ABI leak lint、未授权依赖和 FFI 命中。
- **编译反馈成本：** Growth 每步 cargo check 更频繁，可统计生成阶段 cargo check 次数与后续 repair 迭代次数是否存在替代关系。
- **placeholder 后果：** 统计 `todo!()`、`unimplemented!()`、`panic!("not implemented")` 在 Growth 输出中的出现率，以及 RustTestAgent 后续修复成本。

## 局限与反例

- baseline 之间机制差异较大，不能只比较最终通过率；需要同时报告 LLM 请求轮数、prompt token、repair 迭代和测试迭代。
- `StableRustAgent` 没有 source-level 按需读取，不适合复杂行为项目，但可作为低复杂度 baseline。
- `GrowthRustAgent` 允许 placeholder，这与最终功能等价目标存在张力；论文中应把它作为「增量编译策略」而非最终安全策略。
- 默认 `RustAgent` 已有 contract、API contract、skeleton 和缺失文件补生成，不是一个弱 baseline。
- 如果后续 RustRepairAgent 和 RustTestAgent 非常强，可能掩盖初始生成器差异，需要单独报告「生成后未修复」状态。

## 可写入论文位置

建议放入「实验设置」中的 baseline 描述，也可在「方法演进」中解释为什么最终默认脚本选择 contextual 路径：它比默认 agent 更能控制上下文，比 Stable 保留更多证据，比 Growth 更少依赖 placeholder。

