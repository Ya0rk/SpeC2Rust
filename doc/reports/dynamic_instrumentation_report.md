# 动态插桩实现说明

## 1. 文档目标

本文说明当前 `rtest` 阶段的动态插桩是如何实现的。

这里的「动态插桩」准确来说是「由 LLM 发起的调试器探针」：它不会修改 Rust 或 C 源码，而是通过 LLDB 在目标可执行文件上设置断点、运行程序、采集局部变量、调用栈和 watch 表达式结果，然后把这些证据写回下一轮修复 prompt。

相关核心文件：

- `src/agent/rtest/repair_prompt.py`
- `src/agent/rtest/rust_test_agent.py`
- `src/agent/rtest/log_agent.py`
- `src/agent/rtest/runtime_probe.py`
- `src/agent/rtest/debug_backends.py`

## 2. 入口条件

动态插桩只在 LogAgent 开启时可用。

主流程入口参数：

```bash
--use-log-agent
--log-agent-max-debug-probes 6
```

单独运行 rtest 时：

```bash
USE_LOG_AGENT=1 bash scripts/rtest_agent.sh <project>
```

如果没有开启 LogAgent：

- prompt 中不会出现 `debug_probe` 字段说明。
- LLM 返回 `debug_probe` 也会被忽略。
- 修复流程只依赖 stdout、stderr、bash trace、源码片段和测试产物。

========================== 补充 ========================

我们之前只对少量测例执行过动态插桩

动态插桩比静态插桩的条件更苛刻

动态插桩还需要两个条件

一是我们rust编译要debug模式，这个比较方便

二是c项目也要是debug编译的，需要CC FLAG的时候带-g，这个需要我们手动修改大批量的项目

考虑到之后可能仅对少量大项目进行log agent，因此目前还未修改

========================================================

## 3. LLM 请求格式

当 LogAgent 开启后，`repair_prompt.py` 会在 JSON schema 中暴露 `debug_probe`：

```json
{
  "debug_probe": {
    "target": "rust | c | both",
    "backend": "lldb",
    "targets": {
      "rust": {
        "breakpoints": [
          {
            "file": "src/<your_module>.rs",
            "line": 42
          }
        ],
        "watch_expressions": [
          "state.len()"
        ]
      },
      "c": {
        "breakpoints": [
          {
            "file": "src/source.c",
            "line": 42
          }
        ],
        "watch_expressions": [
          "state"
        ]
      }
    },
    "program_args": [
      "--help"
    ],
    "collect_stack": true,
    "collect_locals": true
  }
}
```

字段含义：

- `target`：探测目标，可选 `rust`、`c`、`both`。
- `backend`：当前主要支持 `lldb`。`dap` / `lldb-dap` 只会额外生成 launch payload，实际采集仍走 LLDB。
- `breakpoints`：断点列表，包含文件路径和真实行号。
- `targets.rust` / `targets.c`：当 `target = both` 时，为 Rust 和 C 分别指定断点与表达式。
- `program_args`：直接传给被调试可执行文件的参数。
- `collect_stack`：是否采集调用栈。
- `collect_locals`：是否采集当前 frame 的局部变量。
- `watch_expressions`：LLDB 表达式列表。

## 4. 当前动态插桩的执行语义

当前实现需要特别注意：动态插桩不会重跑完整 shell 测试脚本。

它的执行方式是：

```text
进入该失败用例的 run_dir
    ↓
直接启动 Rust 或 C 可执行文件
    ↓
传入 LLM 提供的 program_args
    ↓
在 LLDB 中设置断点并运行
    ↓
采集 locals / backtrace / watch expressions
```

因此它适合观察「某个具体 CLI 参数下」的程序内部状态，例如：

```json
{
  "debug_probe": {
    "target": "rust",
    "backend": "lldb",
    "breakpoints": [
      {
        "file": "src/which.rs",
        "line": 180
      }
    ],
    "program_args": [
      "--all",
      "ls"
    ],
    "collect_stack": true,
    "collect_locals": true,
    "watch_expressions": [
      "name",
      "state.path_list.len()"
    ]
  }
}
```

但它不等价于：

```bash
bash test/some_case.sh
```

动态探针不会自动继承测试脚本中复杂的 shell 控制流，也不会自动执行脚本里的前置命令。LLM 如果需要复现某个子命令，必须从 `bash -x` trace 或测试产物里提取对应参数，再放进 `program_args`。

## 5. 从 LLM 请求到执行的完整流程

整体流程如下：

```text
LLM 在修复 JSON 中返回 debug_probe
    ↓
RustTestAgent 解析 JSON
    ↓
检查 LogAgent 是否开启
    ↓
检查该轮是否同时包含 edits 或新材料请求
    ↓
检查 debug_probe 是否有有效断点
    ↓
检查是否达到本用例 debug probe 上限
    ↓
检查是否和上一轮 probe 完全重复
    ↓
RuntimeProbeService.execute_debug_probe()
    ↓
按 target 选择 Rust / C 可执行文件
    ↓
LogAgent.parse_instrumentation_request()
    ↓
LldbBackend 生成 lldb.cmd
    ↓
执行 rust-lldb 或 lldb
    ↓
解析 LLDB 输出
    ↓
写入 .cgr_logs/debug_probe_<round>.json
    ↓
下一轮 prompt 自动读取并展示 probe evidence
```

## 6. RustTestAgent 侧的保护逻辑

动态探针属于「取证轮」，不能和代码修改混在一起。

当前 `rust_test_agent.py` 有以下约束：

### 6.1 LogAgent 未开启时忽略

如果 `enable_log_agent = False`，`debug_probe` 会被清空，并在 history 里提示该字段不受支持。

### 6.2 不能和 edits 同轮执行

如果同一轮 JSON 里既有 `edits` 又有 `debug_probe`：

- rtest 会应用编辑流程优先级。
- `debug_probe` 会被忽略。
- history 会提示：动态探针是证据采集行为，不能和实现修改混在同一轮。

这个规则避免模型「一边猜一边探」，导致下一轮无法分辨证据来自修改前还是修改后。

### 6.3 不能和新材料请求同轮执行

如果同一轮请求了新的 C / Rust / test artifact 材料，同时也请求 `debug_probe`：

- rtest 会先提供材料。
- 同轮 debug probe 会被跳过。
- 下一轮要求 LLM 先阅读新增材料再决定是否探针。

原因是新增源码或测试产物可能已经足够解释失败，不应马上增加调试噪声。

### 6.4 必须有有效断点

`_has_meaningful_debug_probe()` 会检查请求里是否至少存在一个有效断点：

- `file` 非空。
- `line` 是大于 0 的整数。

如果没有有效断点，请求会被跳过。

### 6.5 限制探针次数

每个失败用例最多执行 `max_debug_probes` 次动态探针。

默认值：

```text
6
```

超过后，rtest 会要求模型使用已有证据、静态插桩、源码读取或直接提交 edit。

### 6.6 重复探针检测

`_debug_probe_fingerprint()` 会对整个 `debug_probe` JSON 做稳定哈希。

如果当前请求和上一轮完全一致：

- rtest 会跳过。
- history 会提示不要重复探测同一个位置。

这用于防止 LLM 在相同断点上反复请求，浪费修复轮次。

## 7. RuntimeProbeService 的派发逻辑

动态插桩的主要执行入口是：

```python
RuntimeProbeService.execute_debug_probe(...)
```

它做几件事：

1. 根据失败用例找到 run dir。
2. 创建 `.cgr_logs/` 目录。
3. 解析 `target`。
4. 如果 `target = both`，拆成 Rust 和 C 两个目标执行。
5. 对每个目标调用 `_execute_dynamic_target()`。
6. 汇总结果写入 `debug_probe_<attempt>.json`。

### 7.1 target 选择

目标映射如下：

```text
target = rust
    → Rust release binary: target/release/<bin_name>-rust

target = c
    → CProjectBuilder 构建出的 C 参考可执行文件

target = both
    → 分别执行 rust 和 c 两次 probe
```

### 7.2 target-specific 配置

当 `target = both` 时，LLM 可以这样写：

```json
{
  "debug_probe": {
    "target": "both",
    "targets": {
      "rust": {
        "breakpoints": [
          {
            "file": "src/cat.rs",
            "line": 120
          }
        ],
        "watch_expressions": [
          "pending.len()"
        ]
      },
      "c": {
        "breakpoints": [
          {
            "file": "src/cat.c",
            "line": 430
          }
        ],
        "watch_expressions": [
          "bpout - outbuf"
        ]
      }
    },
    "program_args": [
      "-v",
      "input.txt"
    ]
  }
}
```

`RuntimeProbeService._effective_target_spec()` 会把 `targets.rust` 或 `targets.c` 的内容合并到当前目标的 spec 上，再交给 `LogAgent.parse_instrumentation_request()`。

## 8. LogAgent 如何解析探针请求

`LogAgent.parse_instrumentation_request()` 会把 JSON 请求转成 `InstrumentationRequest`：

```python
InstrumentationRequest(
    target="rust",
    breakpoints=[BreakpointSpec(file="src/main.rs", line=42)],
    collect_stack=True,
    collect_locals=True,
    watch_expressions=["state.len()"],
    program_args=["--help"],
)
```

解析时会做基础清洗：

- 忽略不是对象的断点项。
- 忽略没有 file 的断点。
- 忽略 line 不是正整数的断点。
- `collect_stack` 默认开启。
- `collect_locals` 默认开启。
- `watch_expressions` 和 `program_args` 会转成字符串列表。
- `target` 只允许 `rust`、`c`、`both`。

## 9. LLDB 后端如何运行

当前动态探针真正执行在 `LldbBackend`。

### 9.1 调试器选择

`LldbBackend._resolve_debugger()` 会按顺序查找：

```text
rust-lldb
lldb
```

如果两个都找不到，会抛出：

```text
Neither rust-lldb nor lldb was found on PATH
```

这个错误会被包装进 probe evidence。

### 9.2 生成 LLDB 脚本

`LldbBackend.build_script()` 会生成一个 `lldb.cmd` 文件，大致内容如下：

```lldb
settings set auto-confirm true
settings set stop-disassembly-count 0
target create "/path/to/program"
breakpoint set --file src/main.rs --line 42
settings set target.run-args --help
run
echo __CGR_LOCALS_BEGIN__
frame variable
echo __CGR_LOCALS_END__
echo __CGR_BACKTRACE_BEGIN__
bt
echo __CGR_BACKTRACE_END__
echo __CGR_WATCH_0_BEGIN__
expression -- state.len()
echo __CGR_WATCH_0_END__
process kill
quit
```

几个实现细节：

- `target create` 的 program 路径使用 JSON 字符串转义。
- `program_args` 用 `shlex.quote()` 拼入 `settings set target.run-args`。
- 每段采集结果前后都有 `__CGR_*_BEGIN__` / `__CGR_*_END__` 标记，方便后续解析。
- 最后执行 `process kill` 和 `quit`，避免被调试进程残留。

### 9.3 执行方式

LLDB 以 batch 模式执行：

```bash
rust-lldb --batch -s <run_dir>/.cgr_logs/debug_probe_round_<n>/<target>/lldb.cmd
```

执行工作目录是当前失败用例的 run dir。

超时时间计算方式：

```text
max(10, min(60, test_timeout_seconds + 15))
```

也就是说：

- 最少 10 秒。
- 最多 60 秒。
- 默认测试超时 30 秒时，debug probe 超时约 45 秒。

## 10. LLDB 输出解析

`LldbBackend.parse_output()` 会解析 LLDB stdout 中被 marker 包围的段落：

```text
__CGR_LOCALS_BEGIN__
...
__CGR_LOCALS_END__

__CGR_BACKTRACE_BEGIN__
...
__CGR_BACKTRACE_END__

__CGR_WATCH_0_BEGIN__
...
__CGR_WATCH_0_END__
```

### 10.1 locals 解析

locals 来自：

```lldb
frame variable
```

解析逻辑比较轻量：

- 匹配形如 `name = value` 的行。
- 存入 `locals[name] = value`。
- 无法解析的行放入 `_raw`。

因此它适合读取简单变量，但复杂结构体、枚举、引用、指针的展示质量取决于 LLDB 输出本身。

### 10.2 backtrace 解析

backtrace 来自：

```lldb
bt
```

解析逻辑会提取：

- frame index。
- 原始 frame 文本。
- 尝试提取 function。
- 尝试提取 file 和 line。

如果某行无法按标准 frame 格式解析，也会保留原始文本。

### 10.3 watch expression 解析

watch expression 来自：

```lldb
expression -- <expr>
```

结果按序号保存：

```json
{
  "watch_values": {
    "0": "...",
    "1": "..."
  }
}
```

这里的 key 是表达式序号，不是表达式文本本身。

## 11. 证据文件结构

一次动态探针执行后，典型目录结构如下：

```text
<case_run_dir>/
    .cgr_logs/
        runtime.json
        debug_probe_3.json
        debug_probe_round_3/
            rust/
                lldb.cmd
            c/
                lldb.cmd
```

如果请求了 `backend = "dap"` 或 `backend = "lldb-dap"`，还会生成：

```text
.cgr_logs/
    debug_probe_3.rust.dap.json
```

但这个 DAP 文件目前只是 launch payload，不是实际执行结果。实际执行结果仍来自 LLDB。

## 12. debug_probe 结果格式

`RuntimeProbeService._execute_dynamic_target()` 返回的单目标结果包含：

```json
{
  "target": "rust",
  "backend": "lldb",
  "executed_backend": "lldb",
  "program": "/path/to/target/release/foo-rust",
  "ok": true,
  "returncode": 0,
  "command": [
    "rust-lldb",
    "--batch",
    "-s",
    "/path/to/lldb.cmd"
  ],
  "script_path": "/path/to/lldb.cmd",
  "stdout_tail": "...",
  "stderr_tail": "...",
  "frames": [],
  "locals": {},
  "watch_values": {}
}
```

如果 `target = both`，外层结果会包含：

```json
{
  "target": "both",
  "request": {},
  "probe_round": 3,
  "targets": {
    "rust": {},
    "c": {}
  },
  "ok": true
}
```

如果 `target = rust`，为了兼容旧逻辑，外层还会把 Rust 单目标字段平铺到顶层。

## 13. 下一轮 prompt 如何读取证据

每轮修复开始时，如果 LogAgent 开启，`RustTestAgent` 会调用：

```python
RuntimeProbeService.read_runtime_evidence(failing_case)
```

它会读取：

- `.cgr_logs/runtime.json`
- 最近最多 4 个 `debug_probe_*.json`
- 最近 1 个 `static_probe_*.json`

其中 DAP payload 文件会被排除，因为它不是执行结果。

这些证据会进入 prompt 的 `[Runtime evidence]` 区块。

## 14. 动态插桩的适用场景

动态插桩适合以下问题：

- 程序在某个参数下走错分支。
- 输出错误，但 stdout / stderr 看不出内部状态。
- 需要确认 Rust 是否进入了和 C 相同的函数路径。
- 需要比较 C / Rust 在某个关键点的变量值。
- 当前源码和测试产物都不足以判断 bug。

典型例子：

- `which`：观察 PATH 搜索时 command name、candidate path、状态位。
- `cat`：观察 CR / LF 处理中的 pending 状态。
- `head`：观察参数解析后的行数、字节数、文件读取状态。
- `shc`：观察 generator 对 shell、argv、临时文件路径的解析状态。

## 15. 当前实现的关键限制

### 15.1 不是完整 shell 测试重放

动态探针直接启动目标可执行文件，不会自动执行失败的 `.sh` 脚本。

因此，如果失败依赖：

- shell 函数。
- 复杂重定向。
- pipe。
- FIFO。
- `PATH` 临时修改。
- 多进程时序。
- 脚本创建的中间文件。

则动态探针可能无法直接复现失败。此时应先读取 `bash -x` trace 和测试产物，必要时用静态插桩或让测试脚本自然运行。

### 15.2 release binary 可能缺少调试信息

当前 Rust 目标来自：

```text
target/release/<bin_name>-rust
```

如果 release 构建没有 debug info：

- 文件行号断点可能不稳定。
- `frame variable` 可能拿不到局部变量。
- 变量名可能被优化掉。
- watch expression 可能失败。

如果后续要提高动态插桩质量，可以考虑专门增加一个 debug/probe build profile，例如：

```toml
[profile.release]
debug = true
```

或者单独构建 debug 版本供探针使用。

### 15.3 watch expression 依赖 LLDB 上下文

`watch_expressions` 是 LLDB 表达式，不是 Rust/C 源码片段。它必须在断点停住的 frame 中合法。

如果表达式引用了不存在的变量、被优化掉的变量，或 Rust 表达式无法被 LLDB 理解，结果会失败或为空。

### 15.4 target-specific program_args 目前不是分别配置

当前 `program_args` 是整个 probe 请求级别的字段。`target = both` 时，Rust 和 C 默认使用同一组参数。

如果 C / Rust 需要不同参数，目前需要分两轮分别请求。

### 15.5 DAP 只是 payload，不是实际后端

虽然请求 `backend = "dap"` 时会生成 DAP launch JSON，但当前真正执行仍然是 `LldbBackend`。

这意味着目前系统还没有真正接入 debug adapter 的交互式执行和变量采集。

## 16. 与静态插桩的区别

动态插桩：

- 不改源码。
- 依赖 LLDB。
- 适合单点断点观察。
- 更适合快速查看调用栈和局部变量。
- 对 debug info 和优化级别敏感。

静态插桩：

- 修改临时复制项目，不污染原项目。
- 通过 `eprintln!` / `fprintf(stderr, ...)` 打日志。
- 适合跨多次运行观察固定变量。
- 更接近真实执行路径。
- 需要表达式能通过目标语言编译。

实际使用建议：

- 如果只是想看某个函数是否进入、局部变量是多少，先用动态插桩。
- 如果失败依赖 shell 脚本环境、文件系统状态或复杂时序，优先用测试产物和静态插桩。
- 如果 C / Rust 需要长期对比同一变量，使用静态插桩更稳定。

## 17. 后续可优化方向

### 17.1 从 bash trace 自动生成 probe program_args

当前 LLM 必须自己从 trace 中提取参数。后续可以自动识别最后一次项目命令调用，把参数候选提供给 LLM，或者作为默认 probe args。

### 17.2 支持以测试脚本方式运行 debug probe

更完整的方案是让 LLDB attach 到测试脚本启动的 Rust 进程，或者让 wrapper 在启动 Rust 时进入 LLDB。

这可以覆盖：

- pipe。
- FIFO。
- `PATH` 修改。
- 多进程测试。
- 脚本生成的临时文件。

但实现复杂度明显更高。

### 17.3 增加 probe build profile

可以为 rtest 动态探针增加专门构建模式：

- 保留 debug info。
- 降低优化级别。
- 保持 release 行为尽量一致。

这样可以提高断点和 locals 的可用性。

### 17.4 watch_values 使用表达式文本作为 key

当前 watch 结果按 `"0"`、`"1"` 编号保存。后续可以同时保存表达式文本，便于 LLM 直接关联：

```json
{
  "expression": "state.len()",
  "value": "3"
}
```

### 17.5 对 LLDB 错误做更细分类

当前 LLDB stdout / stderr 会被保留，但没有对常见错误做结构化分类。后续可以识别：

- 找不到断点文件。
- 行号无符号信息。
- 表达式解析失败。
- 进程正常退出但没停到断点。
- 调试器不可用。

这样能帮助 LLM 判断是 probe 写错了，还是程序路径没有走到。

## 18. 总结

当前动态插桩的本质是：LLM 通过 `debug_probe` 请求一个 LLDB batch 调试任务，rtest 在失败用例的 run dir 中直接启动 Rust 或 C 可执行文件，按指定断点采集 locals、backtrace 和 watch expression，然后把结果写回下一轮 prompt。

它的优点是实现简单、不会污染源码、能快速获取运行时状态，并且支持 Rust / C / both 三种目标。

它的主要限制是没有完整重放 shell 测试脚本，且依赖 release binary 的调试信息质量。因此，在复杂测试里，动态插桩应和测试产物读取、bash trace、静态插桩配合使用，而不是单独承担所有诊断工作。
