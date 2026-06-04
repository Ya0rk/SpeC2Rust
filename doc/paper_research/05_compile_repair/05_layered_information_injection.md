# 代码修复中的分层信息注入机制

## 研究结论

代码修复阶段的信息注入不是把编译错误、源码和测试输出一次性塞进 prompt，而是把信息按任务边界、错误信号、项目结构、诊断计划、源码材料、历史状态、工具协议和验证反馈分层注入。这样做的核心价值是：先用强约束信息收窄修复目标，再用按需读取的源码材料补足证据，最后用结构化编辑和真实编译 / 测试反馈校验每一轮改动。

当前实现没有把各类信息写成固定百分比。`RustRepairAgent` 的读取材料在 `_materialize_read_requests()` 和 `_materialize_search_requests()` 中按 LLM 的诊断计划动态物化，调用处传入 `max_chars=None`，所以编译修复侧不设硬性材料比例。`RustTestAgent` 的功能修复侧有 `MaterialBudget`，默认 `PROMPT_MATERIAL_BUDGET_CHARS = 256000`，但它管理的是 C 源码、Rust 源码和测试产物材料的合计字符预算，也不是固定比例。论文中应把比例写成“结构性分布统计口径”或“经验区间”，并用日志实际测量，而不是声称系统内置了固定分配。

## 分层信息注入的实现方式

分层信息注入由两条修复链共同构成：

- 编译修复链：`src/agent/rust_repair_agent.py` 中的 `RustRepairAgent`，负责把 `cargo check` 和 `cargo build --release` 的失败转化为多轮结构化修复。
- 功能修复链：`src/agent/rtest/rust_test_agent.py`、`repair_prompt.py`、`test_runner.py` 等 rtest 模块，负责把 C 项目 shell 测试失败转化为材料请求、取证、编辑、重建和回归检查闭环。

两条链的共同模式是：

1. 先注入不可变任务边界，例如 Rust 项目路径、C 项目路径、测试脚本只读、C/spec 只读、只能编辑 Rust 项目。
2. 再注入当前失败信号，例如编译错误批次、测试 stdout/stderr、bash trace、失败子用例。
3. 然后注入项目结构和可读材料索引，告诉模型哪些 Rust/C/spec/test artifact 可读。
4. 要求模型先输出诊断或动作 JSON，按需请求材料，而不是直接猜测修改。
5. 系统把材料物化为带行号的源码片段、整文件、搜索结果或运行证据。
6. 最后要求模型用结构化编辑协议提交修改，并用真实构建和测试反馈进入下一轮。

## RustRepairAgent 的注入层次

| 层级 | 信息类型 | 主要实现位置 | 注入内容 | 作用 |
| --- | --- | --- | --- | --- |
| L0 | 任务边界层 | `configure_context_sources()`、`repair_project()` | Rust 工程根目录、C 源目录、`c_docs`、PointerAgent / MacroAgent 证据开关、最大迭代次数 | 限定修复对象和可读证据边界，避免把只读 C/spec 当成可编辑目标 |
| L1 | 编译错误层 | `_run_single_iteration()`、`_select_repair_error_batch()` | `cargo check` 输出、错误数量、活跃错误批次、`grouped_errors`、错误签名 | 把修复目标从全项目收窄到当前最值得处理的一组编译错误 |
| L2 | 项目结构层 | `_build_project_overview()` | Rust 文件树、可读 Rust/C/spec 上下文列表、重要文件摘要 | 给模型建立项目地图，减少无效读取请求 |
| L3 | 诊断计划层 | `_build_diagnosis_prompt()`、`_request_diagnosis_plan()` | `target_files`、`read_requests`、`search_requests`、`edit_strategy`、诊断理由 JSON | 把“需要看什么证据”和“如何编辑”拆开，降低无证据猜改 |
| L4 | 材料物化层 | `_materialize_read_requests()`、`_materialize_search_requests()`、`_format_material_inventory()` | Rust whole file / line range、C 文件片段、spec 文档、搜索结果、真实行号、材料清单 | 提供可引用、可定位的修复证据，使编辑可以落到真实行号 |
| L5 | 历史状态层 | `_request_handoff_summary()`、`repair_journal.jsonl`、`current_summary`、`handoff_summary` | 上轮改动摘要、错误前沿、失败签名、已尝试策略、轮内材料新增记录 | 防止重复试错，帮助跨轮保持因果链 |
| L6 | 编辑协议层 | `_build_repair_tool_protocol()`、`_build_edit_prompt()`、`_request_structured_edits()` | `replace_range`、`insert_after`、`delete_range`、`copy_range_after`、`copy_c_string_array_after`、`create_file` 等 JSON 协议 | 把自由文本修复变成可审计、可拒绝、可回滚的结构化操作 |
| L7 | 本地验证层 | `_apply_structured_edits_with_audit()`、`_run_cargo_check()`、`_compile_success_result()` | 编辑审计、stub/破坏性编辑过滤、post-check 输出、release build 结果、停滞检测 | 用真实工具链验证修复，失败信号回流到下一轮 |

这套层次中，L1 到 L4 是“证据注入”，L5 是“记忆注入”，L6 是“动作约束注入”，L7 是“验证反馈注入”。编译修复的关键不是一次给足所有上下文，而是让模型先说明读取计划，再由系统按计划把 Rust/C/spec 材料物化出来。

## RustTestAgent 的注入层次

| 层级 | 信息类型 | 主要实现位置 | 注入内容 | 作用 |
| --- | --- | --- | --- | --- |
| L0 | 测试目标层 | `RustTestAgent.run()` | C 项目路径、Rust 项目路径、推断出的 `bin_name`、Rust release binary、C reference binary、测试脚本只读规则 | 明确 rtest 是行为等价修复，不是修改测试来通过 |
| L1 | 测试失败层 | `TestRunner.run_single()`、`run_all()` | exit code、stdout/stderr tail、耗时、timeout 状态、失败摘要 | 提供黑盒失败事实，定位当前待修复用例 |
| L2 | trace / 子用例层 | `capture_trace_for()`、`_capture_trace()`、`focused_failure` | `bash -x` trace 尾部、当前 unresolved subcase、测试命令展开、最后执行到的 shell 分支 | 把 shell 脚本矩阵中的失败点展开为可定位场景 |
| L3 | 首轮种子材料层 | `extract_test_flags()`、`extract_test_keywords()`、`seed_c_sources()`、`seed_rust_files()` | 测试 flag、关键字、期望输出、相关 C 源记录、相关 Rust 文件、测试脚本和产物 | 在第一轮就给出高相关证据，减少模型盲目请求 |
| L4 | 材料预算层 | `MaterialBudget`、`build_repair_prompt()` | `provided_c_records`、`provided_rust_files`、`provided_test_artifacts`、材料表、淘汰记录、预算压力摘要 | 控制 prompt 体积并保留可追踪材料清单 |
| L5 | 运行取证层 | `RuntimeProbeService`、`LogAgent`、`debug_probe`、`static_probe_update` | `runtime.json`、debug probe、static probe、C/Rust 双侧观测值 | 当源码和测试材料不足以解释行为差异时，注入动态证据 |
| L6 | 动作协议层 | `RepairResponseContract`、`build_repair_prompt()` | `edits`、`cgr_read`、`rust_read`、`test_artifact_read`、`material_keep`、`debug_probe`、`static_probe_update`、`complete` | 约束每轮只能请求材料、取证或编辑，避免混乱动作 |
| L7 | 验证 / 回归层 | `_build_and_verify()`、`_check_regression()`、`ProjectSnapshot` | rebuild 结果、当前用例重跑结果、baseline 通过用例回归、快照回滚、失败签名停滞提示 | 把“当前用例通过”升级为“不破坏已有通过用例” |

rtest 的信息注入更偏向行为修复。它不仅注入源码，还注入测试执行环境、wrapper 映射、shell trace、运行产物和回归警告。其目标是让模型理解“为什么 Rust CLI 行为和 C CLI 行为不同”，而不是只修复语法或类型错误。

## 信息分布和比例口径

现有系统没有固定比例，所以论文中建议采用两种表述：

1. 实现表述：信息注入由动态请求和预算控制决定，不写死比例。
2. 实验表述：按 prompt block 或日志材料字符数统计每类信息占比，报告均值、中位数和项目间方差。

### 编译修复侧的结构性分布

| 信息类别 | 经验占比区间 | 形成机制 | 说明 |
| --- | ---: | --- | --- |
| 编译错误与错误组织 | 20% 到 30% | `cargo check` 输出、`grouped_errors`、活跃错误批次 | 这是修复目标层，比例随错误数量和批大小变化 |
| Rust 源码材料 | 30% 到 45% | `read_requests(kind=rust)`、行范围、整文件、编辑后刷新 | 通常是最大材料来源，因为最终编辑落在 Rust 文件 |
| C/spec 对照材料 | 10% 到 25% | `read_requests(kind=c/spec)`、Pointer/Macro 证据、搜索结果 | 接口、常量、宏、字符串数组和语义对照依赖这部分 |
| 项目概览 | 5% 到 10% | `_build_project_overview()` | 提供路径地图和可读上下文索引，通常较稳定 |
| 历史与前沿状态 | 5% 到 10% | `current_summary`、`handoff_summary`、journal 摘要 | 随轮次增加而增长，但应保持摘要化 |
| 工具协议和安全规则 | 15% 到 25% | 编辑 JSON schema、路径约束、防 stub 规则 | 属于固定 prompt 成本，复杂协议下占比会偏高 |

这些比例是结构性估计，不是代码常量。真实统计应从 `repair_journal.jsonl` 中读取每轮 `materials`、`content_chars`、`stage` 和 prompt block，按块名累加字符数。

### 功能修复侧的结构性分布

| 信息类别 | 经验占比区间 | 形成机制 | 说明 |
| --- | ---: | --- | --- |
| 测试失败输出与 trace | 15% 到 25% | stdout/stderr tail、`PROMPT_TRACE_TAIL_CHARS`、focused failure | 失败越复杂，trace 占比越高 |
| Rust 源码材料 | 25% 到 40% | `seed_rust_files()`、`rust_read`、编辑后刷新 | 默认是主要编辑证据 |
| C 源码对照材料 | 10% 到 25% | `seed_c_sources()`、`cgr_read`、C source index | 用于恢复 C 语义、flag 行为和边界条件 |
| 测试脚本与产物 | 5% 到 15% | `test_artifact_read`、timeout artifacts、fixture 摘要 | 用于理解测试期望，但禁止作为编辑目标 |
| runtime / probe 证据 | 0% 到 20% | LogAgent、debug probe、static probe | 只在开启或请求取证时出现，所以占比可为 0 |
| 协议、预算表和安全规则 | 15% 到 25% | `build_repair_prompt()`、`MaterialBudget` 表、反作弊规则 | 这是稳定的动作约束成本 |
| 历史、回归和停滞反馈 | 5% 到 15% | `history_summary`、`regression_warning`、stall signature | 后期轮次和回归场景中占比升高 |

rtest 侧可以直接利用 `MaterialBudget.total_chars()`、`budget_pressure_summary()`、新增 / 淘汰事件和 prompt 中的材料表做统计。由于 `MaterialBudget` 只统计材料区，不覆盖测试失败、规则和 trace 等固定块，论文统计时还需要额外解析 prompt 标题块。

## 各层信息的作用

| 作用 | 对应层次 | 工程意义 |
| --- | --- | --- |
| 收窄搜索空间 | L0、L1、L2 | 把全项目修复变成当前错误批次或当前失败用例修复 |
| 建立语义对照 | L3、L4、L5 | 通过 C/spec/test/runtime 证据恢复 C 到 Rust 的行为关系 |
| 降低幻觉编辑 | L3、L4、L6 | 要求模型先请求真实材料，再用真实行号提交编辑 |
| 保持过程可审计 | L5、L6、L7 | journal、材料清单、结构化编辑和验证结果都可复盘 |
| 防止测试污染 | rtest L0、L6、L7 | 测试脚本和 fixture 只读，当前用例通过后仍要查回归 |
| 推动多轮进展 | L5、L7 | 失败签名、前沿和交接摘要让下一轮避免重复同一策略 |

## 论文中的可测量指标

建议把“分层信息注入”写成一个可实验验证的机制，而不是只写系统设计：

1. 按层统计 prompt 字符数和材料字符数，报告各层均值、中位数、P90 和项目间方差。
2. 统计每轮 `read_requests`、`search_requests`、`cgr_read`、`rust_read`、`test_artifact_read` 的数量，衡量动态材料请求是否减少无效上下文。
3. 统计 Rust/C/spec/test/runtime 五类材料在成功修复轮和失败停滞轮中的占比差异。
4. 对比关闭 C/spec 材料、关闭首轮 seed、关闭 runtime probe、关闭回归检查后的成功率和轮数变化。
5. 统计结构化编辑被拒绝的原因，例如非法路径、测试编辑、假实现、破坏性缩短、无效行号。
6. 统计验证反馈带来的状态变化，例如 `post_check` 错误数量下降、当前用例通过、回归触发、快照回滚、失败签名重复。

## 写作建议

论文中可以把这部分命名为“Hierarchical Evidence Injection for Translation Repair”。重点不是强调 prompt 很长，而是强调信息有层次、有权限、有预算、有状态、有验证。编译修复侧体现“编译器诊断驱动的证据读取和结构化编辑”，功能修复侧体现“测试失败驱动的行为证据注入和回归保护”。两者合起来构成翻译项目的后处理修复闭环。

## 追加：信息块内容与来源明细

下面按真实 prompt 构造路径展开说明“每个部分包含什么内容、这些内容从哪里提取”。这里的“来源”分为 4 类：

1. 运行时来源：`cargo check`、`cargo build --release`、shell 测试 stdout/stderr、`bash -x` trace、timeout 产物、probe 产物。
2. 文件系统来源：Rust 工程、原始 C 项目、`c_docs` / spec 文档、测试目录、运行目录。
3. 中间状态来源：LLM 上一轮 JSON、`MaterialBudget`、`repair_journal.jsonl`、`ProjectSnapshot`、`history_summary`。
4. 固定协议来源：代码里写死的 read/edit JSON schema、安全规则、反作弊规则、只读边界规则。

### 编译修复：诊断 prompt 的信息块

`RustRepairAgent` 先构造诊断 prompt，让模型只输出“读什么、搜什么、优先修什么”的 JSON 计划。对应函数是 `src/agent/rust_repair_agent.py` 的 `_build_diagnosis_prompt()`。

| prompt 部分 | 包含什么内容 | 内容从哪里提取 | 物化 / 组织方式 | 为什么注入 |
| --- | --- | --- | --- | --- |
| 任务角色 | “正在做 Rust compile-repair diagnosis，不要输出代码” | 固定协议文本 | 直接写入 `_build_diagnosis_prompt()` | 强制诊断阶段和编辑阶段分离 |
| 可读接口说明 | `whole_file`、`line_range`；字段包括 `kind=rust/c/spec`、`path`、`mode`、`start_line`、`end_line` | 固定协议文本 | 直接写入 prompt | 告诉模型只能通过结构化请求读取材料 |
| 搜索接口说明 | `search_requests`；字段包括 `kind=rust/c/spec/all`、`query`、`path_glob`、`context_lines`、`max_results` | 固定协议文本 | 直接写入 prompt | 当不知道文件路径时，先搜索再读文件 |
| 关键约束 | 禁止 fallback、minimal stub、空行为实现；缺业务模块时必须先读 C/spec/Rust 证据 | 固定协议文本 | 直接写入 prompt | 防止模型为了编译通过创建假实现 |
| 可选 Pointer / Macro 证据协议 | 可读的 pointer/macro spec 证据、启用条件和读取规则 | `configure_context_sources()` 注入的 `c_docs_path`、PointerAgent / MacroAgent 开关 | `_optional_evidence_protocol()` 根据 `_context_evidence_enabled()` 判断是否展示 | 只在存在相关证据时暴露，避免默认 prompt 被低相关文档淹没 |
| Project overview | Rust 项目文件、`Cargo.toml`、`src/lib.rs`、C 源码可读列表、spec 可读列表 | Rust 工程目录、`c_project_path`、`c_docs_path` | `_build_project_overview()` 扫描 `src/*.rs`，记录文件大小和前 1 到 8 行首行；`_list_context_files()` 递归列出允许读取的 C/spec 文件 | 给模型一张项目地图，减少无效路径请求 |
| Previous round experience summary | 上一轮做过什么、哪些策略有效 / 无效、下一轮应避免什么 | 上一轮结束时生成的 `handoff_summary` | `_request_handoff_summary()` 生成；下一轮传回 `_build_diagnosis_prompt()` | 让跨轮修复保留经验，避免重复试错 |
| Error organization context | 当前活跃错误批次的摘要、批次编号、诊断数量、上下文窗口 | `cargo check` 输出经 `_select_repair_error_batch()` 处理；如果启用 ErrorOrganizerAgent，还包括 organizer 选出的批次 | 存到 `self._current_error_batch_context`，再作为代码块注入 | 告诉模型为什么当前只看这一批错误，并给出批内上下文 |
| Current error | 当前要修的错误块，按文件或批次组织，最多展示前 6 个错误块 | 当前轮 `cargo check` 输出 | `_select_repair_error_batch()` 选择错误；`grouped_errors.items()[:6]` 展示；多余块折叠成 omission note | 保持 prompt 聚焦，不让派生错误淹没核心错误 |
| 诊断 JSON schema | `summary`、`target_files`、`read_requests`、`search_requests`、`edit_strategy`、`reasoning` | 固定协议文本 | 直接写入 prompt | 让模型输出机器可解析计划，而不是自然语言建议 |

这里的关键点是：诊断 prompt 中真正来自项目的内容主要是 `Project overview`、`Current error`、`Error organization context` 和 `handoff_summary`。读接口、搜索接口和安全约束是固定协议，它们的作用是约束模型如何请求下一层证据。

### 编译修复：Project overview 具体由哪些信息组成

`Project overview` 不是手写摘要，而是从文件系统中提取出来的结构化清单。

| 子块 | 具体内容 | 提取来源 | 提取逻辑 |
| --- | --- | --- | --- |
| `Rust project files:` | `rust:src/<file>.rs`、文件大小、文件前几行中的第一行 headline | 当前 Rust 工程的 `src/` 目录 | `_build_project_overview()` 遍历顶层 `src/*.rs`，用 `os.path.getsize()` 取大小，用 `_read_file_slice(project_dir, path, 1, 8)` 读取前 8 行，再取第一行作为 headline |
| `rust:Cargo.toml` | Cargo manifest 是否存在 | 当前 Rust 工程根目录 | 如果 `Cargo.toml` 存在，就插入到清单前部 |
| `rust:src/lib.rs` | 库入口是否存在 | 当前 Rust 工程 `src/lib.rs` | 如果 `src/lib.rs` 存在，就插入到清单前部 |
| `Readable C source context (kind=c):` | 可读取的 C 源码相对路径 | `configure_context_sources(c_project_path=...)` 注入的 C 项目根目录 | `_list_context_files(kind="c", limit=40)` 递归扫描，过滤 `.git`、`target`、缓存目录和不允许文件 |
| `Readable spec/c_docs context (kind=spec):` | 可读取的 spec / `c_docs` 文档路径 | `configure_context_sources(c_docs_path=...)` 注入的文档根目录 | `_list_context_files(kind="spec", limit=60)` 递归扫描，并受 Pointer/Macro 证据开关过滤 |
| optional evidence protocol | pointer/macro 证据的读取说明 | PointerAgent / MacroAgent 生成的 spec 文件和启用状态 | `_optional_evidence_protocol()` 根据上下文证据开关追加 |

这部分起到“索引层注入”的作用：它通常不包含完整源码，只告诉模型有哪些材料可以读取，以及哪些路径看起来相关。

### 编译修复：材料物化层的内容来源

诊断 JSON 返回后，系统不会直接相信模型的分析，而是按请求读取真实文件或搜索真实文件。

| 请求类型 | JSON 字段 | 读取来源 | 物化函数 | 注入到下一步的内容 |
| --- | --- | --- | --- | --- |
| Rust whole file | `{"kind":"rust","path":"src/a.rs","mode":"whole_file"}` | 当前修复运行目录，也就是 Rust 工程副本或原地工程 | `_materialize_read_requests()` | 文件全文，后续在编辑 prompt 中带真实行号显示 |
| Rust line range | `{"kind":"rust","path":"src/a.rs","mode":"line_range","start_line":10,"end_line":80}` | 当前 Rust 工程 | `_materialize_read_requests()` | 指定行范围内容，保留 `start_line` / `end_line` 元数据 |
| C whole file / line range | `kind=c` | 原始 C 项目根目录 | `_materialize_read_requests()` 通过上下文根目录解析 | C 源文件全文或行范围，只读，不允许作为编辑目标 |
| spec whole file / line range | `kind=spec` | `c_docs` / spec 文档根目录 | `_materialize_read_requests()` 通过上下文根目录解析 | 规格、模块说明、pointer/macro 证据等，只读 |
| 全域搜索 | `{"kind":"all","query":"xxx","path_glob":"**/*"}` | Rust 工程、C 项目、spec 文档 | `_materialize_search_requests()` | 搜索命中的文件路径、行号附近片段、query 信息 |
| 分域搜索 | `kind=rust/c/spec` | 对应根目录 | `_materialize_search_requests()` | 只在指定域中返回命中片段 |

材料进入编辑 prompt 之前会被转成两类形式：

- 普通文件材料：通过 `_format_material_with_line_numbers()` 加上 `NNNN |` 真实行号，提示模型编辑时必须使用这些行号。
- 搜索结果材料：以 `mode=search_results` 注入，包含 query、命中文件和片段，用于决定下一轮是否读取更大范围。

`_format_material_inventory()` 还会生成材料清单，记录 `kind:path`、行范围或搜索标识以及内容字符数。这个清单不是给模型阅读源码用的，而是帮助模型判断“当前 prompt 已经有哪些材料，不要重复请求”。

### 编译修复：编辑 prompt 的信息块

编辑 prompt 是第二阶段 prompt，对应 `_build_edit_prompt()`。它把诊断计划、真实材料、错误块和编辑协议合并，让模型输出可执行编辑 JSON。

| prompt 部分 | 包含什么内容 | 内容从哪里提取 | 组织方式 | 为什么注入 |
| --- | --- | --- | --- | --- |
| Requirements | 18 条以上修复规则，例如只能 JSON、禁止 `replace_file`、未读文件不能改、禁止 stub、缺模块先读证据 | 固定协议文本 | 直接写入 `_build_edit_prompt()` | 把安全边界、编辑边界和修复策略写死 |
| Tool protocol | `replace_range`、`delete_range`、`insert_before`、`insert_after`、`copy_range_after`、`cp`、`copy_c_string_array_after`、`create_file`、`create_dir` 的字段说明 | 固定协议文本和当前项目路径 | `_build_repair_tool_protocol(project_dir)` 生成 | 把自然语言修改转成可审计操作 |
| Diagnosis plan | 上一步 LLM 输出的 `summary`、`target_files`、`read_requests`、`search_requests`、`edit_strategy`、`reasoning` | `_request_diagnosis_plan()` 解析出的 JSON；解析失败时用 fallback 读错误文件计划 | `json.dumps(diagnosis_plan, ensure_ascii=False, indent=2)` 原样放入 | 保持两阶段之间的因果链 |
| Known summary for current round | 轮内已有阅读、搜索、编辑、编译结果的摘要 | 当前轮 `updated_summary` 或系统追加反馈 | `current_summary` 作为代码块注入 | 轮内多次 action 时避免忘记刚读 / 刚改的事实 |
| Cross-round handoff summary | 上一轮总结 | `_request_handoff_summary()` 生成 | `handoff_summary` 作为代码块注入 | 跨轮记忆 |
| Error organization context | 当前错误批次摘要 | `_current_error_batch_context` | 代码块注入 | 让编辑动作继续围绕当前错误批次 |
| Read material inventory | 当前材料 ID、路径、范围、字符数 | `materials` 列表 | `_format_material_inventory(materials)` | 防止重复读材料，并帮助判断是否已具备编辑证据 |
| Related errors | 与当前诊断目标相关的错误块，优先展示 `target_files`，最多 6 个文件；结构严重损坏时追加警告 | `grouped_errors` 和 `diagnosis_plan.target_files` | 先按 target_files 排序，再补其他错误；如果出现多个 delimiter 错误则追加 severe structural brace damage note | 把编辑和当前错误对齐 |
| Read materials | 真实 Rust/C/spec 文件内容、行范围内容、搜索结果 | `_materialize_read_requests()` 和 `_materialize_search_requests()` 的结果 | 文件材料带 `NNNN |` 行号；搜索材料按 query 展示 | 这是模型提交真实行号编辑的主要证据 |
| Return JSON schema | `summary`、`edits`、`more_read_requests`、`search_requests`、`complete`、`updated_summary` | 固定协议文本 | 直接写入 prompt | 要求模型输出下一步动作，而不是自由文本 |

编辑 prompt 中“可变内容”的核心是 5 个块：`Diagnosis plan`、`Read material inventory`、`Related errors`、`Read materials`、`summary_block`。其余块主要是固定协议和安全边界。

### 编译修复：轮内增量和验证反馈来自哪里

编辑 prompt 返回后，系统进入“动作执行和反馈回流”。

| 反馈类型 | 来源 | 处理位置 | 下一轮如何注入 |
| --- | --- | --- | --- |
| `more_read_requests` | LLM 编辑 JSON | `_run_single_iteration()` 中读取 `structured.get("more_read_requests")` | 通过 `_materialize_read_requests()` 转成新材料，追加到 `materials`，下一次编辑 prompt 展示 |
| `search_requests` | LLM 编辑 JSON | `_run_single_iteration()` 中读取 `structured.get("search_requests")` | 通过 `_materialize_search_requests()` 转成搜索结果，追加到 `materials` |
| 结构化 edits | LLM 编辑 JSON | `_apply_structured_edits_with_audit()` | 应用后记录 audit；相关文件材料刷新，post-check 后继续下一轮 |
| 编辑审计 | 编辑工具返回 | `_apply_structured_edits_with_audit()` 和 journal 写入点 | 写入 `repair_journal.jsonl`，用于复盘和后续统计 |
| post-check 编译结果 | 编辑后再次运行 `cargo check` | `_run_single_iteration()` 的 `post_check` 阶段 | 新的 `cargo check` 输出重新进入 `_select_repair_error_batch()`，成为下一次错误层注入 |
| release build 结果 | `cargo check` 通过后运行 `cargo build --release` | `_compile_success_result()` | 最终成功条件；失败时作为编译修复仍未完成的证据 |
| 停滞信号 | 错误签名和错误数量窗口 | `_error_signature()`、轮内 stall 检测 | 进入 `current_summary` / journal，提示后续换策略 |
| 跨轮 handoff | 当前轮摘要、错误变化、有效 / 无效编辑 | `_request_handoff_summary()` | 下一轮诊断 prompt 和编辑 prompt 都会注入 |

因此，编译修复的信息注入是闭环的：`cargo check` 产生错误，错误组织成批次，批次驱动读取，读取驱动编辑，编辑后再次 `cargo check`，新的编译输出再回到错误层。

### 功能修复：rtest 修复 prompt 的信息块

功能测试修复由 `src/agent/rtest/repair_prompt.py` 的 `build_repair_prompt()` 生成 prompt。它比编译修复多了测试执行环境、shell trace、C reference、运行产物和回归约束。

| prompt 部分 | 包含什么内容 | 内容从哪里提取 | 组织方式 | 为什么注入 |
| --- | --- | --- | --- | --- |
| Test script runtime conventions | 测试脚本只读、项目同名命令映射到 Rust binary、`$C_BIN` 才是 C reference、不污染 `PATH`、不要硬编码路径 | 固定协议文本，基于 `TestRunner` 的真实 wrapper 设计 | 直接写入 `build_repair_prompt()` | 防止模型误解测试环境或试图改测试 |
| Inferred tested features | flag candidates、keyword candidates | 测试脚本名和脚本文本 | `_repair_failing_case()` 调用 `extract_test_flags()`、`extract_test_keywords()` | 帮助模型先找对应 C/Rust 代码路径 |
| Current failing subcase | 最小失败块、diff 或最近失败摘要 | 当前 `TestCaseResult` 的 stdout/stderr/trace | `_focused_failure_block(failing_case)` 生成 | 让模型优先看当前失败点，而不是泛读整脚本 |
| Current unresolved subcase | 从最新 `bash -x` trace 推断的当前未解决子场景 | `runner.capture_trace_for()` 捕获的 trace 和脚本文本 | `_trace_subcase_context()` 生成 | shell 测试常有多子用例，trace 能定位最后执行分支 |
| Test script | 当前失败 `.sh` 的完整文本 | Rust 工程 `test/` 中复制来的只读脚本 | `_read_script_text(failing_case.script_path)` | 给模型理解测试期望和 fixture 使用方式 |
| Most recent execution result | exit code、stdout tail、stderr tail | `TestRunner.run_single()` 返回的 `TestCaseResult` | prompt 中按字段展示 | 注入黑盒失败事实 |
| bash trace block | `bash -x` 尾部，最多 `PROMPT_TRACE_TAIL_CHARS` | `capture_trace_for()` 或失败后 `run_single(capture_trace=True)` | `trace_block` 代码块 | 展示真实执行命令和最后失败位置 |
| runtime evidence | `runtime.json`、debug/static probe 摘要 | LogAgent / RuntimeProbeService 产物 | `_build_runtime_evidence_block()` | 当源码不足以解释行为差异时注入运行时证据 |
| instrumentation context | 当前已激活 static probes、probe schema | `state.static_probes` 和 LogAgent 开关 | `_build_instrumentation_context()` | 让模型知道已有 probe 和可请求 probe 类型 |
| expected-output snippets | 从脚本中提取的期望输出片段 | `.sh` 脚本 heredoc 或比较文本 | `extract_expected_outputs()` 后截断展示 | 用于理解行为，但同时提醒禁止硬编码 |
| build error block | 上一轮编辑后 `cargo build --release` 失败输出尾部 | `_build_and_verify()` 中的 cargo build 结果 | `last_build_error[-BUILD_ERROR_TAIL_CHARS:]` | 功能修复中出现编译错误时优先修编译 |
| regression warning | 上一轮让当前用例通过但破坏 baseline 的详情 | `_check_regression()` 的失败结果和 rollback 过程 | `regression_warning[-REGRESSION_WARNING_TAIL_CHARS:]` | 把回归用例变成硬约束 |
| Project structure design document | 生成阶段的项目结构设计 | `.cgr_generation_plan.json` 中的 `project_structure`，没有则扫描项目 | `project_structure` 代码块 | 给模型了解 Rust 模块设计意图 |
| Rust project overview | Rust 工程文件概览 | 当前 Rust 工程目录 | `_build_rust_project_overview()` | 帮助模型选择要读 / 改的 Rust 文件 |
| C source index | 可请求的 C 函数 / 文件索引 | C source records JSON 或 C 项目扫描结果 | `load_source_records()` 后由 `build_source_index_display()` 展示 | 支持 `cgr_read` 按函数或文件请求 C 证据 |
| Readable test-run artifacts | 当前运行目录下可读产物列表 | `TestCaseResult.run_dir` | `_list_test_artifacts(failing_case)` | 让模型知道可以请求 `.out`、`.err`、timeout、生成源码等产物 |
| Prompt material budget status | 总预算、当前材料字符数、淘汰事件 | `MaterialBudget` | `budget_pressure_summary()` | 暴露材料压力和 LRU 淘汰事实 |
| Currently provided material table | 当前 prompt 已包含的 C/Rust/test 材料 ID | `MaterialBudget` 内部材料表 | `material_manifest()` | 防止重复请求，指导 `material_keep` |
| Latest material request status | 上一轮材料请求哪些新增、哪些已有、哪些不可读 | `_absorb_material_requests()` 和 `_absorb_test_artifact_requests()` 的结果 | `_format_material_request_feedback()` | 把材料请求反馈给模型 |
| Provided C source code | C 函数记录、文件片段或 whole file，带真实 C 行号 | 首轮 `seed_c_sources()` 或后续 `cgr_read` | `material.c_records()` 转成 `NNNN |` 代码块 | 作为 Rust 行为修复的语义 oracle |
| Provided Rust files | Rust 文件全文或行范围，带真实 Rust 行号 | 首轮 `seed_rust_files()` 或后续 `rust_read_requests` | `material.rust_file_entries()` 转成 `NNNN |` 代码块 | 作为编辑目标证据 |
| Provided test-run artifacts | 测试运行产物文本 | 首轮 `_seed_test_artifacts()` 或后续 `test_artifact_read` | `material.test_artifact_entries()` 代码块 | 补充 stdout 摘要之外的完整失败证据 |
| Repair memory | 上轮 summary、协议错误、材料反馈、stall 提示、probe 提示 | `state.history_summary` | 文本注入 | 让多轮修复保持状态 |
| JSON contract | `summary`、`cgr_read`、`rust_read_requests`、`test_artifact_read`、`edits`、`material_keep`、`debug_probe`、`static_probe_update`、`complete` | 固定协议文本；probe 字段受 LogAgent 开关影响 | 直接写入 prompt | 约束模型输出可执行动作 |

这张表说明 rtest 的 prompt 并不是“失败输出 + 源码”的简单拼接，而是至少混合了 7 类信息：测试执行约定、失败事实、shell trace、C oracle、Rust 编辑材料、测试产物、历史 / 回归 / probe 状态。

### 功能修复：首轮材料从哪里来

rtest 在进入 LLM 前会主动注入一批高相关材料，不等模型第一轮盲猜。

| 材料 | 提取来源 | 提取逻辑 | 注入位置 |
| --- | --- | --- | --- |
| flags | 当前失败脚本名和脚本文本 | `extract_test_flags(failing_case.name, script_content)` 从 CLI 选项形态中提取 | `Inferred tested features` |
| keywords | 当前失败脚本名和脚本文本 | `extract_test_keywords()` 提取关键字符串、子命令、错误词 | `Inferred tested features`，并用于 seed |
| expected outputs | 当前 `.sh` 脚本 | `extract_expected_outputs()` 提取 heredoc / 期望输出片段，超过阈值的跳过 | `expected-output snippets` |
| C seed records | C source index | `seed_c_sources(flags, source_index, keywords, limit=SEED_C_LIMIT)` 打分选取，默认最多 4 个 | `Provided C source code` |
| Rust seed files | Rust 工程 `src/**/*.rs` | `seed_rust_files(flags, rust_project_path, keywords, limit=SEED_RUST_LIMIT)` 打分选取，默认最多 3 个 | `Provided Rust files` |
| test artifacts seed | 当前失败用例 run dir | `_seed_test_artifacts()` 按关键字和文件类型挑选小型相关产物 | `Provided test-run artifacts` |
| bash trace | 当前失败脚本 | 首次全量测试不抓 trace；进入修复时 `runner.capture_trace_for()` 懒加载 | `bash trace block` 和 `subcase_context` |

这层首轮材料的目的，是让第一轮 prompt 就包含“测试在测什么、C 是怎么做的、Rust 哪些文件可能相关、运行时实际失败在哪里”。

### 功能修复：后续材料请求从哪里来

LLM 返回 JSON 后，rtest 会按不同字段从不同来源补材料。

| JSON 字段 | 允许请求什么 | 来源 | 系统处理 | 下一轮如何呈现 |
| --- | --- | --- | --- | --- |
| `cgr_read` | C 函数、C 文件 whole file、C 文件 line range | C source index 和原始 C 项目 | `_absorb_material_requests()` 调用 `CSourceIndex.fulfill_request()` | 加入 `MaterialBudget.c_records()`，带真实 C 行号 |
| `rust_read_requests` | Rust 文件 whole file 或 line range | 当前 Rust 工程 | `_absorb_material_requests()` 读取并校验路径 | 加入 `MaterialBudget.rust_file_entries()`，带真实 Rust 行号 |
| `test_artifact_read` | 当前 run dir 中的日志、输出、timeout 文件、生成源码等 | `TestCaseResult.run_dir` | `_absorb_test_artifact_requests()`，路径必须留在 run dir 内 | 加入 `MaterialBudget.test_artifact_entries()` |
| `debug_probe` | Rust/C/both 的断点和运行参数 | Rust release binary、C reference binary、当前失败脚本 | `RuntimeProbeService.execute_debug_probe()` | 写入 `.cgr_logs/debug_probe_<attempt>.json`，下一轮作为 runtime evidence |
| `static_probe_update` | 临时插桩点、观测表达式、program args | Rust/C 项目临时副本 | `RuntimeProbeService.execute_static_probes()` | 写入 `.cgr_logs/static_probe_<attempt>.json`，下一轮作为 instrumentation evidence |
| `edits` | Rust / Cargo 源文件结构化编辑 | 当前 Rust 工程 | `RepairAdapter.apply_structured_edits()`，并复用 RustRepairAgent 编辑能力 | 编辑后刷新 Rust 材料，再进入 build/test 验证 |
| `material_keep` | 希望保留的材料 ID | 模型对当前材料表的选择 | 只作为优先级提示，不立即硬删 | 预算压力下由 `MaterialBudget` LRU 机制处理 |

这里需要强调：`test_artifact_read` 是证据读取，不是测试编辑；`debug_probe` 和 `static_probe_update` 是取证动作，不能和 `edits` 混在同一轮，否则系统会忽略 probe 或跳过编辑。

### 功能修复：验证反馈从哪里回流

| 反馈 | 来源 | 生成位置 | 如何进入下一轮 |
| --- | --- | --- | --- |
| rebuild 失败 | `cargo build --release` 输出 | `_build_and_verify()` | 写入 `state.last_build_error`，下一轮进入 `build error block` |
| 当前用例仍失败 | `runner.run_single(capture_trace=True)` | `_build_and_verify()` | 更新 `failing_case.stdout/stderr/trace`，下一轮进入失败块和 trace |
| 当前用例通过 | 当前失败脚本重跑结果 | `_build_and_verify()` | 触发 `_check_regression()`，不是立即接受 |
| baseline 回归 | baseline 中原本通过的脚本重跑失败 | `_check_regression()` | 格式化为 `state.regression_warning`，回滚后下一轮注入 |
| 快照回滚 | `ProjectSnapshot.restore()` | 回归或单 case 最终失败时 | 回滚后 rebuild/restage；回归详情保留到下一轮 |
| 失败签名停滞 | 连续多轮相同 `failure_signature()` | `_build_and_verify()` | 写入 `history_summary`，要求下一轮换策略 |
| 协议错误 | LLM 输出非 JSON 或字段不合规 | `RepairResponseContract` | 写入 `history_summary`，下一轮要求修正协议 |
| 材料请求反馈 | 新增 / 已有 / 不可读材料统计 | material request absorption 后 | 写入 `state.material_request_feedback`，下一轮提示模型不要重复请求 |

rtest 的验证反馈比编译修复多一层“回归保护”。因此功能修复中的信息注入不仅回答“为什么当前失败”，还回答“为什么某个修复不能接受”。

### 可用于统计“内容从哪里来”的落地口径

如果论文需要量化“哪些内容占 prompt 的多少”，建议按以下来源分类统计，而不是只按 Rust/C/test 三类材料统计：

| 来源类别 | 统计对象 | 统计方法 |
| --- | --- | --- |
| 固定协议 | Requirements、JSON schema、runtime conventions、安全规则、反作弊规则 | 按 prompt 中固定标题块或固定字符串范围计字符数 |
| 编译 / 测试失败信号 | `Current error`、stdout/stderr、trace、focused failure、build error | 从 prompt 对应代码块计字符数；也可从 `TestCaseResult` 和 `cargo` 输出原始字段计 |
| 项目索引 | Project overview、Rust overview、C source index、test artifact index | 按索引块字符数计 |
| Rust 材料 | `kind=rust` read material、provided Rust files | 编译修复从 `materials.kind == "rust"` 统计；rtest 从 `MaterialBudget.rust_file_entries()` 统计 |
| C 材料 | `kind=c` read material、C records | 编译修复从 `materials.kind == "c"` 统计；rtest 从 `MaterialBudget.c_records()` 统计 |
| spec 材料 | `kind=spec` read material、Pointer/Macro 文档 | 编译修复从 `materials.kind == "spec"` 统计 |
| test artifact 材料 | provided test-run artifacts、timeout 文件、`.out/.err/.log` | rtest 从 `MaterialBudget.test_artifact_entries()` 统计 |
| runtime / probe 证据 | `runtime.json`、`debug_probe_*.json`、`static_probe_*.json` | 按 runtime evidence block 字符数或 `.cgr_logs` 文件大小统计 |
| 历史状态 | `handoff_summary`、`history_summary`、`regression_warning`、material feedback | 按对应 prompt block 字符数统计 |
| 动作结果 | edits、audit、post-check、regression rollback | 从 `repair_journal.jsonl`、rtest 输出和状态字段统计 |

最终可以报告两类比例：

1. prompt 内占比：每类信息在发送给 LLM 的 prompt 字符数中占多少。
2. 材料池占比：只看动态材料池时，Rust/C/spec/test/runtime 各占多少。

这两个比例不能混用。prompt 内占比会包含大量固定协议和安全规则；材料池占比只反映动态读取证据，不代表完整 prompt 的全部信息结构。
