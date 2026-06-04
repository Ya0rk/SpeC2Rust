# LogAgent 与 rtest 修复机制说明

================ 补充 ==================

log agent包括静态插桩和动态插桩，先看这个文档说明，再看dynamic_instrumentation_report

========================================

## 1. 文档目标

本文整理当前 `LogAgent` 的功能边界，以及 `rtest` 模块在测试迁移、失败诊断、LLM 修复、回归保护方面的主要机制。

这份文档面向两类用途：

- 给后续开发者快速理解测试修复流程。
- 给研究报告或 PPT 提供「为什么 rtest 不只是跑测试，而是一个带证据闭环的修复系统」的材料。

相关核心文件：

- `src/agent/rtest/log_agent.py`
- `src/agent/rtest/runtime_probe.py`
- `src/agent/rtest/repair_prompt.py`
- `src/agent/rtest/rust_test_agent.py`
- `src/agent/rtest/test_runner.py`
- `src/agent/rtest/constants.py`
- `scripts/rtest_agent.sh`

## 2. LogAgent 的定位

`LogAgent` 不是一个独立的代码修复 agent。它的职责是把测试运行时产生的证据整理成 LLM 可以理解、可以继续追问、可以用于对比的上下文。

在当前设计里，`LogAgent` 主要服务于 `RustTestAgent` 的测试修复阶段。它解决的问题是：

- 测试失败后，只看 stdout / stderr 往往不够。
- 有些错误需要观察函数栈、局部变量、关键分支变量。
- 有些错误需要同时对比 C 可执行文件和 Rust 可执行文件的运行状态。
- 证据不能无限塞进 prompt，必须压缩、裁剪、结构化。
- 插桩不能污染原项目源码，尤其不能把调试代码永久留在 Rust 项目里。

因此，`LogAgent` 更准确的名字可以理解为「运行时证据管理层」。

## 3. LogAgent 的核心能力

### 3.1 运行结果结构化

`LogAgent.bundle_from_result()` 会把一个 `TestCaseResult` 转成统一的 `RuntimeEvidenceBundle`，主要包含：

- `case_name`：测试脚本名称。
- `exit_code`：测试退出码。
- `stdout`：标准输出。
- `stderr`：标准错误。
- `trace_lines`：`bash -x` 跟踪行。
- `error`：优先从 stderr 抽取的错误摘要。
- `frames`：调试器采集到的调用栈。
- `locals`：调试器采集到的局部变量。
- `metadata`：额外元信息。

这个结构避免 LLM 直接面对杂乱的 shell 输出。

### 3.2 运行证据压缩

`LogAgent.compress()` 会把运行证据压缩成 prompt 可承受的 JSON 结构：

- stdout 只保留尾部。
- stderr 保留更长的尾部。
- trace 只保留最后若干行。
- frames 限制最多若干层。
- locals 按字符预算裁剪。

这一步的目的不是保存完整日志，而是保留「最可能解释失败原因」的末端证据。

### 3.3 运行时日志文件

当启用 LogAgent 后，`TestRunner` 会在每个测试用例的运行目录下写入：

```text
.cgr_logs/
    runtime.json
```

`runtime.json` 是后续修复轮次的基础证据。`RuntimeProbeService.read_runtime_evidence()` 会读取它，并把最近的 debug probe / static probe 结果合并进下一轮 prompt。

### 3.4 动态调试探针

LLM 可以在修复 JSON 中请求 `debug_probe`。当前支持：

- `target = "rust"`：调试 Rust 可执行程序。
- `target = "c"`：调试 C 参考可执行程序。
- `target = "both"`：同时对 Rust 和 C 侧进行调试。

可配置内容包括：

- 断点文件与行号。
- 观察表达式。
- 是否采集调用栈。
- 是否采集局部变量。
- 程序参数。

实际执行由 `RuntimeProbeService.execute_debug_probe()` 调用调试后端完成。当前动态执行主要依赖 LLDB；如果请求 DAP，会生成 DAP launch payload，但实际采集仍走 LLDB 后端。

动态探针的产物会写到：

```text
.cgr_logs/
    debug_probe_<round>.json
    debug_probe_round_<round>/
```

下一轮 prompt 会自动看到最近的探针摘要。

### 3.5 静态插桩

LLM 也可以请求 `static_probe_update`，用于在源码指定位置临时插入日志语句。

静态插桩的特点：

- 支持 Rust 和 C 两侧。
- 支持多个 probe 持久存在于同一个失败用例的后续修复轮次。
- 支持 `add`、`remove`、`clear`。
- 每个 probe 有稳定 ID，后续可以替换或删除。
- 插桩只作用于临时复制出来的项目，不会修改原始 Rust 项目或 C 项目。

Rust 侧插入的是 `eprintln!`。C 侧插入的是 `fprintf(stderr, ...)`。采集时会过滤带有 `[CGR_STATIC:<id>]` 标记的输出行。

静态插桩适合以下场景：

- 某个变量在多轮测试中都需要观察。
- 需要比较 C / Rust 在同一逻辑点的状态。
- 动态调试器不可用，或者断点位置不稳定。
- 错误不是崩溃，而是输出差异，需要观察中间状态。

### 3.6 LogAgent 关闭时的行为

LogAgent 是可选机制。关闭后：

- prompt 中不会出现 runtime evidence。
- prompt 中不会出现 `debug_probe` / `static_probe_update` schema。
- LLM 即使返回探针请求，`RustTestAgent` 也会忽略。
- 修复仍然可以依靠 stdout、stderr、trace、源码片段和测试产物继续进行。

这点很重要：插桩能力是增强项，不应该污染普通修复路径的 prompt。

## 4. LogAgent 在 rtest 中的工作流

完整证据流如下：

```text
运行测试脚本
    ↓
TestRunner 捕获 stdout / stderr / exit_code / trace
    ↓
LogAgent 写入 runtime.json
    ↓
RustTestAgent 构造下一轮修复 prompt
    ↓
LLM 可以选择：
    - 请求 C 源码
    - 请求 Rust 源码
    - 请求测试产物
    - 请求 debug_probe
    - 请求 static_probe_update
    - 提交代码 edit
    ↓
RuntimeProbeService 执行探针
    ↓
探针结果写入 .cgr_logs/
    ↓
下一轮 prompt 自动携带证据摘要
```

这里的关键约束是：探针是「取证轮」，不应和代码编辑混在同一轮。当前 prompt 和执行逻辑都会提示或强制这个行为。

## 5. rtest 模块整体定位

`rtest` 模块负责把已经翻译出的 Rust 项目放到原 C 项目的 shell 测试体系里验证，并在失败时调用 LLM 修复 Rust 代码。

它不是简单地执行 `cargo test`，而是更接近一个「黑盒功能测试 + 白盒源码修复」系统：

- 黑盒：沿用 C 项目的 `.sh` 测试脚本。
- 白盒：失败后读取 C 源码、Rust 源码、测试产物，要求 LLM 按 C 行为修 Rust。
- 回归保护：当前用例修好后，需要确认之前通过的用例没有被破坏。

## 6. rtest 的测试准备机制

### 6.1 原样复制测试目录

当前 rtest 默认将 C 项目的 `test/` 目录整体复制到 Rust 项目的 `test/` 目录。

这样做的原因是：

- 测试脚本往往依赖同目录 fixture。
- 只复制 `.sh` 容易遗漏输入文件、期望输出、辅助 include 文件。
- 对 coreutils 一类项目，测试脚本和 fixture 的相对路径很重要。

因此，当前策略是尽量保留原测试目录结构。

### 6.2 测试脚本只读

当前 prompt 明确禁止 LLM 修改测试 shell 脚本和 fixture。

这是一个重要设计选择：

- 测试脚本是外部基准，不是待修复对象。
- 如果 LLM 可以改测试，很容易把测试改到通过，而不是修 Rust。
- 脚本适配应由人工预处理完成，而不是在 rtest 修复中动态修改。

因此，LLM 只能修改 Rust 项目源码、`Cargo.toml` 等项目文件，不能编辑 `test/*.sh`。

### 6.3 wrapper 与命令映射

`TestRunner` 会把 C 参考程序和 Rust 程序包装到测试运行目录中，并通过环境变量和 `BASH_ENV` 函数映射暴露给脚本。

当前语义是：

- 项目同名命令默认指向 Rust 可执行程序。
- `$RUST_BIN` 和 `<bin_name>-rust` 指向 Rust 可执行程序。
- `$C_BIN`、`$C_WRAPPER_BIN` 和 `<bin_name>-c` 指向 C 参考程序。
- runner 不会把 wrapper 目录粗暴塞进 `PATH`，避免破坏 `which` 这类测试本身依赖 `PATH` 的项目。

这个设计可以避免两个常见误判：

- 把原脚本里的项目命令误认为 C 程序。
- 用硬编码系统 `PATH` 掩盖 Rust 行为错误。

### 6.4 POSIX 临时运行目录

测试运行目录默认放在类似：

```text
/tmp/cgrcode-rtest/cgrcode-rtest-runs/<project>-<pid>-<hash>/
```

而不是直接放在 `/mnt/e/...` 这样的 Windows 挂载目录下。

原因是 WSL 的 drvfs 经常不支持 FIFO、socket、某些权限位或原子文件行为。像 `cat-buf.sh` 这种依赖 `mkfifo` 的测试，如果放在 `/mnt/e` 下，可能在 Rust 代码还没运行前就失败。

因此，rtest 会尽量把每个用例放到 POSIX 语义更可靠的临时目录里执行。

## 7. rtest 的修复循环

单个失败用例的修复流程如下：

```text
创建项目快照
    ↓
构造初始材料：
    - 失败脚本
    - stdout / stderr
    - bash trace
    - Rust 项目概览
    - 相关 C 函数
    - 相关 Rust 文件
    - 相关测试产物
    ↓
调用 LLM
    ↓
根据 LLM 返回执行：
    - 补充 C 源码材料
    - 补充 Rust 源码材料
    - 补充测试产物
    - 执行 LogAgent 探针
    - 应用 Rust edit
    ↓
cargo build --release
    ↓
重跑当前失败用例
    ↓
如果当前用例通过，检查之前已通过用例是否回归
    ↓
无回归则接受修复
有回归则回滚本轮 edit，并继续修当前用例
    ↓
超过轮数仍未修复，则回滚该用例所有 edits
```

这里的核心原则是：修复目标始终是当前失败用例，但不能破坏已有通过用例。

## 8. rtest 修复 prompt 的特点

### 8.1 明确要求按 C 行为修 Rust

prompt 会告诉 LLM：

- 先理解 C 源码中被测功能的实现。
- 再定位 Rust 缺失或错误的部分。
- 不要凭 Rust 直觉猜输出。
- 不要硬编码 expected output。
- 不要用 `todo!()`、`unimplemented!()`、placeholder 或 stub。

这对 C2Rust 项目很关键，因为目标不是写一个「能过当前字符串」的小程序，而是保持 C 项目的真实行为。

### 8.2 强制 JSON 输出

LLM 必须返回 JSON，主要字段包括：

- `summary`
- `cgr_read`
- `rust_read_requests`
- `test_artifact_read`
- `edits`
- `history_control`
- `debug_probe`
- `static_probe_update`
- `complete`
- `updated_summary`

这样 rtest 可以稳定解析 LLM 意图，而不是从自然语言里猜。

### 8.3 源码读取是显式请求

LLM 不能默认拿到所有源码。它需要显式请求：

- C 函数或 C 文件片段。
- Rust 文件整体或 Rust 行范围。
- 测试运行产物。

这解决了早期上下文过大的问题，也迫使 LLM 说明它需要什么证据。

### 8.4 行号是真实行号

Rust 源码片段进入 prompt 时，会带上：

```text
NNNN | code
```

`NNNN` 是真实文件行号。LLM 提交 `replace_range` / `insert_before` / `insert_after` 时必须使用这些真实行号。

这样可以降低「模型按片段局部行号编辑，导致补丁错位」的问题。

## 9. 材料预算与上下文控制

### 9.1 Prompt 预算

当前 rtest 的材料预算集中在 `PROMPT_MATERIAL_BUDGET_CHARS`，默认是 `256000` 字符，约等于 64k token 级别的材料上限。

预算用于合并控制：

- C 源码材料。
- Rust 源码材料。
- 测试产物材料。

超过预算时，`MaterialBudget` 会进行 LRU 淘汰，并把淘汰情况写进 prompt，提醒 LLM 改用更小的行范围请求。

### 9.2 Rust 行范围去重

当前 `rust_read_requests` 已支持重叠行范围识别。

例如：

- 第一次请求 `src/shc.rs:593-668`。
- 第二次请求 `src/shc.rs:593-900`。

rtest 不会把整个第二段视为重复，也不会重复塞入 `593-668`。它会只补充未覆盖的 `669-900`。

这样可以解决两类问题：

- LLM 重复请求同一文件导致 prompt 膨胀。
- LLM 从小范围扩展到大范围时，被错误判断为「没有新材料」。

### 9.3 旧历史清理

LLM 可以返回：

```json
{
  "history_control": {
    "drop_history": true
  }
}
```

这表示旧修复历史已经干扰判断，需要只保留新的 `updated_summary`。这个机制适合长轮次修复，比如 `shc` 这类生成器项目。

## 10. 测试产物读取机制

rtest 会为每个失败用例维护可读测试产物索引。

LLM 可以通过 `test_artifact_read` 请求：

- `.out`
- `.err`
- `.log`
- `.raw`
- `.x.c`
- `timeout_context.txt`
- `timeout_trace.txt`
- generated source / generated stderr / generated stdout

对于 generated-code 项目，例如 `shc`：

- `.x.c` 是关键证据。
- `shc.stderr` / `a.out.stderr` 是关键证据。
- 不能只盯着 Rust generator 源码猜。

当前规则还要求：如果一轮同时包含 `test_artifact_read` 和 `edits`，优先只提供材料，跳过同轮 edit。原因是测试产物是新证据，必须先让下一轮模型读完证据再修改，避免「边猜边改」。

## 11. 编译优先与测试修复

rtest 的修复阶段以 `cargo build --release` 作为每轮 edit 后的第一道门槛。

如果编译失败：

- 不会继续跑测试。
- 编译错误会进入下一轮 prompt。
- LLM 必须先修编译错误。

如果编译成功：

- 先重跑当前失败用例。
- 当前用例通过后，再检查之前已经通过的 baseline 用例。
- 如果 baseline 回归，当前 edit 会被回滚。

这个策略避免「测试修复阶段生成了无法编译的项目」和「修好一个用例破坏多个用例」。

## 12. 回归保护机制

rtest 会在修复每个失败用例前创建项目快照。

当某一轮 edit 让当前用例通过，但破坏了之前已经通过的用例时：

- rtest 会回滚这一轮 edit。
- 下一轮仍然修当前失败用例。
- prompt 会携带回归警告，要求 LLM 找到同时满足当前用例和回归用例的共享不变量。

这对 `cat-E.sh` / `cat-buf.sh` 这类互相牵制的字节流测试很重要。正确修复不能只是让某个用例通过，而要保留共同语义，例如：

- 已经确定可以输出的字节可以提前 flush。
- 仍处在 lookahead / pending 状态的字节不能提前 finalize。

## 13. 防止无效修复的约束

rtest 当前有多层防护。

### 13.1 禁止修改测试

LLM 对测试脚本、fixture、C 项目文件的 edit 会被拒绝。

### 13.2 禁止 fake 实现

以下行为会被识别或拒绝：

- 写入 `todo!()`。
- 写入 `unimplemented!()`。
- 写入 placeholder panic。
- 把 expected output 长字符串直接硬编码到 Rust 源码。

### 13.3 限制过宽 C 请求

如果 LLM 反复请求过大的 C 文件范围，而不是提出修复计划，rtest 会拒绝并要求改用更聚焦的证据。

一般建议：

- C `line_range` 不超过约 250 行。
- 优先请求具体函数。
- generated-code 项目优先读取生成产物，而不是马上请求整份 C 源码。

### 13.4 重复编辑区域检测

如果同一个失败签名持续存在，而 LLM 多轮编辑同一片代码区域，rtest 会提示停止在该区域反复重写，要求先获取新证据。

这主要用于解决「模型一直改同一个函数但没有进展」的问题。

### 13.5 `complete=true` 不再直接终止失败用例

如果 LLM 返回 `complete=true`，但当前测试仍然失败，rtest 不会直接接受。它会继续下一轮，并要求 LLM 提供新的证据或 edit。

这样可以避免 LLM 用 `complete=true` 逃避无法修复的问题。

## 14. 超时处理

如果测试用例超时，`TestRunner` 会保存额外材料：

```text
timeout_stdout.txt
timeout_stderr.txt
timeout_trace.txt
timeout_context.txt
```

prompt 会提示 LLM 先读取这些 timeout artifact，而不是只根据「timeout」这个词猜测。

这对 `shc` 这类会生成 C 文件、再编译、再运行生成程序的项目尤其重要。超时可能来自：

- Rust 主程序卡住。
- 生成的 C 程序卡住。
- 生成的二进制卡住。
- shell wrapper 参数传递错误。
- 临时文件路径或权限错误。

必须看具体产物。

## 15. rtest 对生成器项目的特殊价值

`shc`、`c4` 这类项目不是简单输入输出函数。它们的 Rust 程序会生成另一个源文件或可执行文件，再由测试脚本运行生成物。

这类项目失败时，真正的 bug 可能不在最终输出，而在中间产物：

- 生成的 `.x.c` 是空文件。
- 生成的 C 没有 `main`。
- 生成的 C 参数传递错位。
- 生成的 shell wrapper 没保留 `$0` / `$1` / `$2`。
- 生成程序编译失败但顶层错误只显示 `a.out not found`。

rtest 当前通过 `test_artifact_read` 和自动产物注入，把这些中间文件提供给 LLM，避免它只在 Rust generator 的大段字符串模板里盲改。

## 16. 当前入口与常用参数

完整主流程中可通过参数启用：

```text
--use-rust-test-agent
--use-log-agent
--log-agent-max-debug-probes <N>
--rust-test-agent-max-iterations <N>
--rust-test-agent-prompt-budget-chars <chars>
```

单独运行 rtest 可以使用：

```bash
bash scripts/rtest_agent.sh which
```

常用环境变量：

```bash
USE_LOG_AGENT=1
LOG_AGENT_MAX_DEBUG_PROBES=6
MAX_REPAIR_ITERATIONS=20
CGR_RTEST_PROMPT_BUDGET_CHARS=256000
TEST_TIMEOUT_SECONDS=30
BUILD_TIMEOUT_SECONDS=600
PYTHON=/path/to/python
```

示例：

```bash
USE_LOG_AGENT=1 MAX_REPAIR_ITERATIONS=64 bash scripts/rtest_agent.sh shc
```

## 17. 现有限制

### 17.1 LogAgent 依赖运行环境

动态调试依赖 LLDB。如果环境没有 LLDB，或者符号信息不足，debug probe 的效果会受限。

### 17.2 静态插桩要求表达式合法

静态 probe 插入的是目标语言表达式：

- Rust 侧表达式必须是合法 Rust。
- C 侧表达式必须是合法 C。

错误表达式会导致临时项目构建失败，失败信息会作为证据返回，但不会污染原项目。

### 17.3 C 侧对比要求 C 项目可构建

如果要对 C 侧执行动态探针或静态插桩，C 项目需要能够在当前环境下构建出参考可执行文件。

### 17.4 LLM 仍可能陷入局部循环

虽然 rtest 已经加入了重复区域检测、材料请求约束、产物读取机制，但复杂生成器项目仍可能出现长时间局部循环。

更可靠的方向是：

- 提高测试拆分粒度。
- 优先提供生成产物。
- 避免大段 escaped C string 模板编辑。
- 把 generator 的逻辑拆成更小的 Rust helper。

## 18. 总结

当前 `LogAgent + rtest` 的核心思路是：让测试失败不再只是一个退出码，而是变成可迭代、可追问、可对比、可回滚的证据闭环。

`LogAgent` 负责把运行时证据、动态调试、静态插桩组织起来。`RustTestAgent` 负责测试执行、材料投喂、LLM edit 应用、编译验证和回归保护。

这套机制的价值在于，它让 C2Rust 项目的后期修复不再完全依赖模型一次性猜对，而是允许模型像工程师一样：

- 看失败现场。
- 读相关源码。
- 读测试产物。
- 必要时插桩。
- 小步修改。
- 编译验证。
- 回归检查。

这也是当前项目从「代码生成」走向「测试驱动修复」的关键基础设施。
