# 术语表

## Agent 与阶段

| 术语 | 含义 | 主要代码 |
| --- | --- | --- |
| `SpecAgent` | 将 C 项目分析结果转换为迁移文档、模块 spec、plan 和 tasks 的 agent | `src/agent/spec_agent.py` |
| `ModuleSplitter` | 将 C 项目按目录、调用关系、结构体共用、函数名前缀和规模阈值拆分为模块/函数簇 | `src/agent/split.py` |
| `ContextualRustAgent` | 按需读取 spec/source/Rust 上下文，并维护符号注册表的 Rust 生成器 | `src/agent/alternatives/contextual_rust_agent.py` |
| `RustRepairAgent` | 基于 `cargo check/test/build` 反馈执行编译修复的 agent | `src/agent/rust_repair_agent.py` |
| `RustTestAgent` | 使用 C 项目 shell 测试验证 Rust 行为，并对失败用例修复的 agent | `src/agent/rtest/rust_test_agent.py` |
| `LogAgent` | 管理测试运行证据、动态探针和静态插桩摘要的证据层 | `src/agent/rtest/log_agent.py` |
| `ErrorOrganizerAgent` | 将大量编译错误归类分批，降低单轮修复 prompt 噪声 | `src/agent/error_organizer_agent.py` |

## 中间产物

| 术语 | 含义 | 常见路径 |
| --- | --- | --- |
| `c_docs` | C 项目理解阶段生成的迁移上下文文档目录 | `output/<project>/c_docs/` |
| `repo manifest` | 仓库边界、文件和模块摘要 | `docs/rewrite-context/00_repo_manifest.md` |
| `spec/plan/tasks` | 模块级迁移目标、实现计划和任务列表 | `c_docs/specs/<module>/` |
| `spec_context.json` | 可选 JSON 中间层，用于机器友好压缩 spec 文档 | `c_docs/spec_json/spec_context.json` |
| `translation_contract` | 用于约束 Rust 生成边界的结构化契约概念；需要调研当前实现与文档中的实际状态 | 见上下文调研文档 |
| `round log` | 每次 LLM 请求/响应的 Markdown 记录 | `log/round_logs/` |
| `repair_journal.jsonl` | Rust 修复过程日志，记录错误签名、编辑、接受原因和前沿状态 | Rust 项目或 run 副本目录 |

## 反馈与判别器

| 术语 | 含义 |
| --- | --- |
| 外部判别器 | 不依赖 LLM 自评的验证机制，包括 `cargo check`、`cargo build`、shell 测试、C 参考程序和 LLDB 探针。 |
| Error signature | 从编译输出中抽取的错误签名，用于判断修复状态是否变化。 |
| Error frontier | 当前修复已推进到的错误前沿；即使错误数未减少，只要暴露更深层错误也可能被接受。 |
| Runtime evidence | 测试运行中采集的 stdout、stderr、bash trace、locals、backtrace、probe 结果等证据。 |
| Dynamic probe | 由 LLM 请求、通过 LLDB 在可执行文件上采集运行时状态的调试探针。 |
| Static probe | 临时在 Rust/C 副本中插入日志语句，运行测试后采集状态，不污染原项目。 |
| Snapshot rollback | 测试修复前保存 Rust 项目快照，若修复造成回归则恢复。 |

## 实验术语

| 术语 | 含义 |
| --- | --- |
| 编译通过率 | `cargo check` 或 `cargo build --release` 通过的项目比例。 |
| 功能通过率 | C 项目 shell 测试在 Rust 项目上通过的比例。 |
| 修复迭代数 | 编译修复或测试修复进入 LLM 循环的次数。 |
| LLM 请求数 | `TranslationMetrics` 统计的模型调用次数。 |
| 代码质量指标 | `unsafe` 行数、裸指针次数、生成文件数、依赖数量、测试是否被修改等。 |
