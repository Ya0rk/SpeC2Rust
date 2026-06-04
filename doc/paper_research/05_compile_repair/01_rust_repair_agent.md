# RustRepairAgent 深度编译修复闭环调研

## 研究问题

`RustRepairAgent` 回答的问题是：当 C 到 Rust 的初始生成结果无法通过 `cargo check` 时，如何把编译器诊断转化为可审计、可回退、尽量不退化的多轮修复闭环。它不是单次「把错误贴给 LLM」的代码修复器，而是把错误分批、上下文读取、结构化编辑、重新编译、前沿验收和交接摘要串成一个状态机。

论文中可以把它定位为编译器反馈驱动的翻译后处理阶段：生成 agent 产出 Rust 工程，`RustRepairAgent` 通过真实编译器反馈推动代码从「文本上像 Rust」进入「可以被 Rust 工具链接受」。

## 流程 / 数据流

默认入口在 `scripts/agent.sh`。默认参数会开启 `--use-rust-repair-agent`、`--use-error-organizer-agent --error-batch-size 10`，并把修复迭代预算设为 `CGR_RUST_REPAIR_MAX_ITERATIONS` 或 64。`src/agent/main.py` 在步骤 3 调用 `run_optional_rust_repair_agent()`，它创建 `RustRepairAgent`，注入 C 源码目录、`c_docs` 目录、PointerAgent / MacroAgent 证据开关，然后对当前 Rust 工程执行原地修复。

核心数据流如下：

1. `repair_project()` 先对原始 Rust 工程运行 `cargo check`，构造基线 `RepairRunResult`。
2. 每轮进入 `_run_single_iteration()`。默认 `in_place=True`，旧的 copy-runs 模式会克隆到 `repair_runs/run-XXX`。
3. 轮开始先执行 `_sanitize_project_locally()`，清理 markdown fence、`thiserror` 残留、内联测试模块，并调用确定性括号修复。
4. 执行 `cargo check`，失败后把错误交给 `_select_repair_error_batch()`；如果注入了 `ErrorOrganizerAgent`，只选择一个活跃批次。
5. `_request_diagnosis_plan()` 要求 LLM 先输出 JSON 诊断计划，只能说明优先文件、读取请求、搜索请求和编辑策略，不能直接改代码。
6. `_materialize_read_requests()` 和 `_materialize_search_requests()` 把 Rust / C / spec 上下文物化成带行号的材料。
7. `_request_structured_edits()` 要求 LLM 输出结构化编辑 JSON，例如 `replace_range`、`insert_after`、`copy_range_after`、`copy_c_string_array_after`、`create_file`。
8. `_apply_structured_edits_with_audit()` 应用编辑并记录审计信息，随后再次 `cargo check`。同一轮内会继续读上下文、搜索、编辑和检查，直到通过、停滞、超时或 LLM 标记本轮完成。
9. 如果 `cargo check` 通过，`_compile_success_result()` 继续跑 `cargo build --release`，用 release build 作为最终通过条件。
10. 每轮结束后，`_request_handoff_summary()` 生成跨轮交接摘要，用于下一轮避免重复试错。

每个运行目录下写入 `repair_journal.jsonl`。样本记录包含 `stage`、`iteration`、`cycle_index`、错误数量、错误签名、诊断计划、材料长度、结构化编辑、审计结果和 `post_check` 结果。`src/tests/_tmp_rust_repair_agent_*/repair_journal.jsonl` 中可见 `clone`、`local_sanitize`、`llm_search_context`、`llm_repair`、`post_check`、`llm_cycle_complete`、`round_timeout`、`iteration_result` 等阶段；当前源码中轮前错误阶段名已是 `pre_llm_no_fallback`。

## 关键工程细节

- **两阶段 LLM 协议。** 先诊断后编辑，诊断 JSON 只允许请求读取和搜索；编辑 JSON 才允许修改文件。这把「看什么证据」和「怎么改」分离，便于审计和复现实验。
- **上下文来源分层。** `kind=rust` 可读写当前 Rust 工程，`kind=c` 只读原 C 工程，`kind=spec` 只读文档和规格。PointerAgent / MacroAgent 只在开启时暴露对应 spec 证据，避免默认 prompt 被额外材料淹没。
- **结构化编辑而非自由整文件输出。** 支持按真实行号替换、删除、插入，也支持从 Rust / C / spec 复制行范围。`copy_c_string_array_after` 可以把 C 字符串数组解析为 Rust `static &[&str]`，避免几百行转义字符串由 LLM 手抄。
- **编辑审计与防护。** 创建文件必须在项目内，禁止 `.git/`、`target/` 和路径逃逸；创建空文件、明显 compile-only stub 会被拒绝；对已有文件的大幅缩短也会被判为破坏性编辑。
- **轮内材料刷新。** 应用编辑后，已读过的 Rust 文件材料会刷新为最新 whole file，避免下一次 LLM 基于旧行号继续改。
- **停滞控制。** 同一轮内如果错误签名连续重复，或错误数量窗口持续不改善，会记录 `error_signature_stall` 并退出本轮。
- **旧版对照。** `CodeFixer` 是早期路径，按 `cargo fmt`、`cargo check`、`cargo build` 三阶段修复，并支持函数级 / 整文件修复。当前默认流程用 `RustRepairAgent` 替代它，但保留 legacy 分支，适合作为论文消融对照。

## 可引用代码证据

| 证据点 | 代码位置 |
| --- | --- |
| 默认开启 Rust 修复、错误分批和 64 轮预算 | `scripts/agent.sh:157`、`scripts/agent.sh:160`、`scripts/agent.sh:165`、`scripts/agent.sh:167` |
| 主流程步骤 3 创建并运行 `RustRepairAgent` | `src/agent/main.py:216`、`src/agent/main.py:241`、`src/agent/main.py:247` |
| `RepairRunResult` 字段和 agent 初始化状态 | `src/agent/rust_repair_agent.py:53`、`src/agent/rust_repair_agent.py:101` |
| 克隆运行目录、journal 路径和 cargo 命令封装 | `src/agent/rust_repair_agent.py:140`、`src/agent/rust_repair_agent.py:158`、`src/agent/rust_repair_agent.py:170`、`src/agent/rust_repair_agent.py:208` |
| 本地清洗和确定性括号修复接入点 | `src/agent/rust_repair_agent.py:400`、`src/agent/rust_repair_agent.py:432` |
| 诊断 prompt 和编辑 prompt 的 JSON 协议 | `src/agent/rust_repair_agent.py:1535`、`src/agent/rust_repair_agent.py:2173` |
| 读取 / 搜索材料物化与工具协议 | `src/agent/rust_repair_agent.py:1729`、`src/agent/rust_repair_agent.py:1925`、`src/agent/rust_repair_agent.py:2077` |
| 结构化编辑审计、防 stub、防破坏性编辑 | `src/agent/rust_repair_agent.py:1201`、`src/agent/rust_repair_agent.py:1265`、`src/agent/rust_repair_agent.py:3276` |
| 单轮修复循环、轮内继续、post-check、停滞检测 | `src/agent/rust_repair_agent.py:3753`、`src/agent/rust_repair_agent.py:3980`、`src/agent/rust_repair_agent.py:4190`、`src/agent/rust_repair_agent.py:4251` |
| 跨轮修复、前沿验收和交接摘要 | `src/agent/rust_repair_agent.py:4355`、`src/agent/rust_repair_agent.py:4439`、`src/agent/rust_repair_agent.py:4449` |
| legacy `CodeFixer` 三阶段修复 | `src/agent/code_fixer_agent.py:884`、`src/agent/code_fixer_agent.py:898`、`src/agent/code_fixer_agent.py:908`、`src/agent/code_fixer_agent.py:918` |

## 实验钩子

- **主指标：** 最终 `cargo check` 通过率、`cargo build --release` 通过率、平均修复轮数、平均 LLM 编辑次数、平均 `cargo check` 次数。
- **journal 统计：** 从 `repair_journal.jsonl` 聚合 `stage` 分布、`llm_repair` 次数、`post_check.error_count_after` 曲线、`iteration_result.accept_reason`、`round_timeout` 和 `error_signature_stall`。
- **消融 1：** `RustRepairAgent` vs legacy `CodeFixer`。使用 `--use-rust-repair-agent` 开关对比。
- **消融 2：** 原地修复 vs copy-runs 前沿保护。对比 `in_place=True` 与 `--copy-runs --apply-best`。
- **消融 3：** 证据读取策略。关闭 C/spec 读取、关闭 PointerAgent / MacroAgent 证据，观察接口类错误修复效率。
- **消融 4：** 结构化编辑协议。对比自由整文件输出与 `replace_range` / `copy_range_after` / `copy_c_string_array_after` 的失败率和破坏性编辑率。

## 局限与反例

- `RepairRunResult.test_passed` 实际表示 `cargo build --release` 是否通过，不是 `cargo test`。论文中应避免把它写成测试通过。
- 默认 `in_place=True` 时，即使 `_should_accept_result()` 认为候选未推进前沿，也不能像 copy-runs 模式那样自动回到旧副本；前沿验收更多是记录和控制下一轮摘要。
- `_error_signature()` 使用 Python 进程内 `hash()`，不同进程间哈希值不可直接比较。跨实验统计应改用稳定哈希，例如 SHA-256。
- `cargo check` 通过后只跑 release build，不能保证行为等价或测试通过；功能正确性需要后续 RustTestAgent / LogAgent 或外部测试集验证。
- 防 stub 规则是启发式：短小真实适配层可能被误判，复杂空实现也可能漏判。
- `_brace_imbalance()` 在结构化编辑审计中是简单字符计数，可能被字符串或注释中的括号影响；更稳妥的做法是复用 `rust_structural_repair` 的 tokenizer。

## 可写入论文位置

- **方法章节：** 「编译器反馈驱动的多轮修复」小节，描述诊断计划、证据物化、结构化编辑和重新编译闭环。
- **系统实现章节：** 「工程防护与可观测性」小节，写 journal、审计记录、路径约束、防 stub、防破坏性编辑。
- **实验章节：** 作为核心消融：有无 `RustRepairAgent`、有无错误分批、有无 copy-runs 前沿保护、有无 C/spec 证据。
