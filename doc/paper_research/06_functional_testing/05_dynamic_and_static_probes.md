# A4-5 动态与静态 probe 调研

## 研究问题

本文调研 rtest 中由 LLM 发起的动态和静态 probe。它们用于在源码和普通测试输出不足时，观察 Rust/C 两侧的运行时状态。动态 probe 通过 LLDB 设置断点并采集调用栈、locals 和 watch expressions；静态 probe 则在临时项目副本中插入日志语句，重新构建并运行目标 binary。

核心研究问题如下：

- LLM 如何以 JSON 协议请求 Rust、C 或 both 目标的 probe？
- 动态 probe 和静态 probe 分别适合哪些失败类型？
- probe 如何避免污染真实 Rust 项目和 C 项目？
- probe 结果如何在下一轮 prompt 中作为证据，而不是和 edits 混淆？

## 流程/数据流

动态 probe 数据流如下：

```text
LLM JSON debug_probe
    -> RustTestAgent 检查 LogAgent 是否开启
    -> 检查是否与 edits 或新材料请求冲突
    -> 检查断点是否有效、是否超过 max_debug_probes
    -> RuntimeProbeService.execute_debug_probe()
    -> target = rust / c / both
    -> LogAgent.parse_instrumentation_request()
    -> LldbBackend.build_script()
    -> rust-lldb 或 lldb --batch 执行
    -> parse_output() 抽取 frames / locals / watch_values
    -> 写 .cgr_logs/debug_probe_<attempt>.json
    -> 下一轮 read_runtime_evidence() 读回
```

静态 probe 数据流如下：

```text
LLM JSON static_probe_update
    -> LogAgent.parse_static_probe_update()
    -> 更新 state.static_probes
    -> RuntimeProbeService.execute_static_probes()
    -> 为 Rust/C 目标复制临时 project
    -> _apply_static_probes() 插入 eprintln! 或 fprintf(stderr)
    -> Rust cargo build --release 或 C clean_and_build
    -> 直接运行 binary + program_args
    -> 过滤 stderr 中 [CGR_STATIC:<id>] 行
    -> 写 .cgr_logs/static_probe_<attempt>.json
    -> 下一轮 prompt 展示 static evidence
```

两类 probe 都是「取证轮」。如果同一轮还包含 edits 或新材料请求，runner 会跳过 probe 或优先提供材料，要求下一轮先分析新证据。

## 关键工程细节

- **目标可选。** `target` 支持 `rust`、`c`、`both`。当 `both` 时，`targets.rust` 和 `targets.c` 可以分别指定断点、表达式和参数。
- **动态 probe 后端。** `backend = "lldb"` 是实际采集路径；若请求 `dap` 或 `lldb-dap`，系统会额外生成 DAP launch payload，但实际执行仍走 LLDB。
- **直接运行 binary。** 动态和静态 probe 都在失败用例 `run_dir` 中直接启动 Rust/C binary，并传入 `program_args`，不会自动重放完整 shell 脚本。
- **断点有效性检查。** `debug_probe` 必须至少包含一个 file 非空、line 大于 0 的断点，否则跳过。
- **probe 次数上限。** 每个失败用例有 `max_debug_probes`，默认 CLI 为 6，避免模型无限请求调试。
- **probe 与 edits 互斥。** 同轮 edits 会导致 `debug_probe` 或 `static_probe_update` 被忽略，保证下一轮看到的证据对应清晰的代码状态。
- **静态 probe 持久于当前 case。** `state.static_probes` 以 probe ID 存储，支持 add、remove、clear；这允许多轮观察同一组关键变量。
- **静态 probe 不污染源码。** 系统复制 Rust/C 项目到 `.cgr_logs/static_probe_round_<attempt>/<target>/project`，插桩只在副本上执行。
- **路径安全。** `_apply_static_probes()` 拒绝绝对路径和包含 `..` 的路径，避免 probe 写出项目副本。
- **C/Rust 插桩差异。** C 侧用 `fprintf(stderr, ...)`，必要时自动插入 `#include <stdio.h>`；Rust 侧用 `eprintln!`。结果通过 `[CGR_STATIC:<id>]` 标记筛选。
- **证据裁剪。** 动态 probe 只保留 stdout/stderr tail、frames、locals、watch values；静态 probe 只保留最后 40 条静态标记行。

## 可引用代码证据

- `src/agent/rtest/repair_prompt.py:828`：prompt 暴露 `debug_probe` JSON schema。
- `src/agent/rtest/repair_prompt.py:838`：prompt 暴露 `static_probe_update` JSON schema。
- `src/agent/rtest/repair_prompt.py:852`：要求 `debug_probe` 只在源码/测试材料不足时使用。
- `src/agent/rtest/repair_prompt.py:854`：说明 static probes 的 add、remove、clear 和临时副本语义。
- `src/agent/rtest/rust_test_agent.py:1314`：解析 `debug_probe` 或兼容字段 `instrumentation`。
- `src/agent/rtest/rust_test_agent.py:1315`：解析 `static_probe_update`。
- `src/agent/rtest/rust_test_agent.py:1325`：edits 与 `static_probe_update` 同轮时忽略 static probe。
- `src/agent/rtest/rust_test_agent.py:1332`：edits 与 `debug_probe` 同轮时忽略 debug probe。
- `src/agent/rtest/rust_test_agent.py:1352`：处理 `static_probe_update` 并执行 static probes。
- `src/agent/rtest/rust_test_agent.py:1386`：处理 `debug_probe`。
- `src/agent/rtest/rust_test_agent.py:1394`：达到 `max_debug_probes` 上限后跳过新请求。
- `src/agent/rtest/runtime_probe.py:90`：`execute_debug_probe()` 是动态 probe 入口。
- `src/agent/rtest/runtime_probe.py:103`：根据 `target` 展开 Rust、C 或 both。
- `src/agent/rtest/runtime_probe.py:133`：动态 probe 结果写入 `debug_probe_<attempt>.json`。
- `src/agent/rtest/runtime_probe.py:137`：`execute_static_probes()` 是静态 probe 入口。
- `src/agent/rtest/runtime_probe.py:149`：静态 probe round 目录位于 `.cgr_logs/static_probe_round_<attempt>`。
- `src/agent/rtest/runtime_probe.py:262`：静态 probe 复制项目副本并应用插桩。
- `src/agent/rtest/runtime_probe.py:293`：静态 probe 直接运行 binary 和 `program_args`。
- `src/agent/rtest/runtime_probe.py:311`：只收集包含 `[CGR_STATIC:` 的 stderr 行。
- `src/agent/rtest/runtime_probe.py:374`：`_apply_static_probes()` 按文件分组插入 probe。
- `src/agent/rtest/runtime_probe.py:381`：拒绝绝对路径和 `..`。
- `src/agent/rtest/runtime_probe.py:398`：`_render_static_statement()` 生成 C/Rust 插桩语句。
- `src/agent/rtest/debug_backends.py:99`：LLDB script 生成入口。
- `src/agent/rtest/debug_backends.py:105`：为每个断点生成 `breakpoint set --file ... --line ...`。
- `src/agent/rtest/debug_backends.py:121`：为每个 watch expression 生成 LLDB expression。
- `src/agent/rtest/debug_backends.py:152`：优先解析 `rust-lldb`，否则 `lldb`。
- `src/agent/rtest/log_agent.py:90`：解析动态 instrumentation request。
- `src/agent/rtest/log_agent.py:117`：解析 static probe update。

## 实验钩子

- **动态 vs 静态对比。** 对同一失败用例分别使用 `debug_probe`、`static_probe_update` 和不用 probe，比较修复轮数。
- **target 对比。** 比较 `target = rust`、`target = c`、`target = both` 对定位 C/Rust 行为差异的帮助。
- **LLDB 可用性。** 记录 `rust-lldb` 或 `lldb` 不存在、断点未命中、locals 为空等失败类型。
- **直接 binary 限制。** 构造需要 shell 前置状态的测试，观察 probe 只传 `program_args` 时是否误导模型。
- **probe 上限消融。** 调整 `--log-agent-max-debug-probes`，比较调试收益和无效探针噪声。
- **静态插桩编译失败率。** 统计 C/Rust 表达式非法导致的 build failure，以及 build failure 是否仍能作为证据进入下一轮。
- **临时副本开销。** 记录 static probe 复制、插桩、构建、运行总耗时，和项目规模的关系。

## 局限与反例

- 动态 probe 不执行完整 shell 脚本，只直接运行 binary；复杂管道、环境变量、临时文件准备需要从 trace 中手动提取。
- LLDB 对 release binary 的 locals 支持有限，优化后变量可能不可见或显示为不可用。
- C 和 Rust watch expressions 使用不同语言语义，LLM 容易给出一侧合法、另一侧非法的表达式。
- 静态 C 插桩把表达式 cast 到 `long long`，适合整数类变量，不适合字符串、结构体、指针内容等复杂值。
- 静态 probe 在临时副本上重新构建，开销较大；对大型项目或生成代码项目可能明显拖慢修复循环。
- probe ID 持久于当前 failing case，不跨 case 复用；如果套件层切换目标，需要重新建立 probe。

## 可写入论文位置

- **方法章节：主动运行时取证。** 把 probe 描述为 LLM 在修复循环中可调用的诊断动作。
- **系统设计章节：动态 probe 与静态 probe 双通道。** 说明断点调试和临时源码插桩的互补关系。
- **工程优化章节：证据污染控制。** 强调 probe 与 edits 互斥、静态 probe 只作用于临时副本。
- **实验章节：probe 消融。** 报告使用 Rust/C/both probe 时的修复轮数、成功率和开销。
