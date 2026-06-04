# A4-1 RustTestAgent 功能测试修复闭环调研

## 研究问题

本文调研 `RustTestAgent` 在翻译项目中的功能测试职责：它如何把原 C 项目的 shell 测试转化为 Rust 项目的功能验证基准，如何在失败后组织源码、测试产物、运行证据和 LLM 修复动作，并如何把「通过当前用例」约束为「不破坏已有通过用例」。

核心研究问题如下：

- 原始 C 测试脚本如何保持为只读基准，而不是被 LLM 改写成容易通过的测试？
- `RustTestAgent` 如何从黑盒测试失败中提取白盒修复上下文？
- 修复循环如何区分「请求材料」「执行 probe」「提交编辑」「编译验证」「回归检查」等动作？
- 哪些工程策略可以降低 prompt 噪声、假实现、重复编辑和停滞修复？

## 流程/数据流

主流程可以抽象为以下数据流：

```text
C 项目路径 + Rust 项目路径
    -> 推断 binary name
    -> 构建 C 参考可执行文件
    -> 复制 C 测试目录到 Rust 项目 test/
    -> 准备测试框架 shim
    -> 收集原始 .sh 脚本
    -> cargo build --release
    -> TestRunner stage Rust/C wrapper
    -> 首次 run_all
    -> 若失败，进入 SuiteRepairCoordinator
    -> 对单个失败用例执行 repair loop
    -> 编辑 Rust 源码
    -> rebuild + 重跑当前用例
    -> 回归检查
    -> 失败则继续，成功则返回套件层重跑
```

单个失败用例的修复数据流更细：

```text
TestCaseResult
    -> 懒加载 bash -x trace
    -> extract_test_flags / keywords / expected_outputs
    -> seed C source / Rust source / test artifact
    -> ProjectSnapshot.create()
    -> build_repair_prompt()
    -> LLM JSON response
    -> 过滤非法编辑和假实现
    -> 吸收 C/Rust/test material requests
    -> 可选 debug_probe / static_probe
    -> apply_structured_edits()
    -> cargo build --release
    -> run_single 当前用例
    -> _check_regression 保护 baseline pass cases
```

这里的关键点是：`RustTestAgent` 不把功能测试看作一次性判定，而是把失败转化为可迭代的证据闭环。每一轮要么增加可验证材料，要么执行受控取证，要么修改 Rust 实现；空转会被写入历史反馈。

## 关键工程细节

- **测试脚本只读。** `--translate-tests` 仍保留为兼容参数，但实际逻辑明确忽略该选项，测试脚本由人工预处理后作为只读基准使用。LLM 不允许改 `.sh` 或 fixture。
- **C 参考先构建。** 修复前先构建 C 项目并定位参考 binary，之后 RuntimeProbeService 可以同时访问 C 和 Rust 两侧。
- **功能测试不是 `cargo test`。** Rust 项目先 `cargo build --release`，再运行 C 项目带来的 shell 脚本。这更接近真实 CLI 行为验证。
- **失败信号分层。** 失败用例会被拆成 stdout/stderr tail、bash trace、当前 unresolved subcase、测试脚本、C 源码索引、Rust 文件窗口、测试产物索引等多层证据。
- **材料预算有状态。** `MaterialBudget` 管理 C 记录、Rust 文件片段和测试产物，`material_keep` 只作为优先级提示，不立即硬删除材料，避免后续轮次基于过期片段修复。
- **编辑边界受控。** `_is_editable_rust_path()` 只允许 `.rs`、`Cargo.toml`、`Cargo.lock`、`build.rs` 等项目文件，拒绝 test、target、wrapper 和 shell 脚本。
- **反作弊过滤。** `extract_expected_outputs()` 从 heredoc 里提取期望输出，`violates_no_fake_impl()` 拒绝 `todo!()`、`unimplemented!()`、占位 panic，以及直接硬编码较长 expected output。
- **取证轮和编辑轮互斥。** `debug_probe`、`static_probe_update`、新增材料请求和 edits 之间有优先级约束，避免同一轮既修改实现又采集修改前后不清的证据。
- **编译失败也进入上下文。** 编辑后如果 `cargo build --release` 失败，编译错误会写入下一轮 prompt，并刷新相关 Rust 文件材料。
- **停滞检测。** `TestCaseResult.failure_signature()` 对失败输出做归一化并哈希，连续多轮同签名失败时要求模型换策略，而不是继续改同一区域。

## 可引用代码证据

- `src/agent/rtest/rust_test_agent.py:124`：`run()` 是 rtest 主入口。
- `src/agent/rtest/rust_test_agent.py:137`：先调用 `CProjectBuilder.clean_and_build()` 构建 C 参考程序。
- `src/agent/rtest/rust_test_agent.py:154`：整体复制 C 测试目录到 Rust 项目的 `test/`。
- `src/agent/rtest/rust_test_agent.py:160`：`--translate-tests` 被明确忽略，说明测试脚本是只读基准。
- `src/agent/rtest/rust_test_agent.py:171`：Rust 侧先执行 `cargo build --release`。
- `src/agent/rtest/rust_test_agent.py:185`：构造 `TestRunner`，并传入测试超时和 LogAgent 开关。
- `src/agent/rtest/rust_test_agent.py:203`：失败后进入 `SuiteRepairCoordinator` 做套件级修复。
- `src/agent/rtest/rust_test_agent.py:1022`：失败用例修复前懒加载 `bash -x` trace。
- `src/agent/rtest/rust_test_agent.py:1024`：从测试脚本提取 flag、keyword 和 expected output。
- `src/agent/rtest/rust_test_agent.py:1050`：单用例修复前创建 `ProjectSnapshot`。
- `src/agent/rtest/rust_test_agent.py:1173`：启用 LogAgent 时读取运行时证据进入 prompt。
- `src/agent/rtest/rust_test_agent.py:1263`：`material_keep` 只作为提示，不做硬剪枝。
- `src/agent/rtest/rust_test_agent.py:1274`：过滤超出 Rust 项目可编辑范围的 edits。
- `src/agent/rtest/rust_test_agent.py:1281`：过滤假实现和硬编码 expected output。
- `src/agent/rtest/rust_test_agent.py:1314`：解析 `debug_probe` 和 `static_probe_update`。
- `src/agent/rtest/rust_test_agent.py:1443`：编辑后进入 `_build_and_verify()`。
- `src/agent/rtest/rust_test_agent.py:1997`：当前用例通过后检查 baseline pass cases 是否回归。
- `src/agent/rtest/rust_test_agent.py:2077`：使用失败签名做停滞检测。
- `src/agent/rtest/repair_prompt.py:728`：要求 LLM 基于真实行号提交编辑。
- `src/agent/rtest/repair_prompt.py:738`：prompt 明确禁止编辑测试脚本、fixture 和 C 项目。

## 实验钩子

- **端到端成功率。** 记录首次测试通过率、修复后通过率、每个项目的失败用例数和最终失败数。
- **修复轮数。** 记录每个 failing case 的 LLM 轮数、材料请求次数、编辑次数、probe 次数。
- **消融 1：关闭 LogAgent。** 对比 `--use-log-agent` 开关对修复轮数和成功率的影响。
- **消融 2：禁用首轮材料注入。** 去掉 seed C/Rust/test artifacts，比较模型是否更频繁请求材料或做猜测性编辑。
- **消融 3：禁用反作弊过滤。** 统计是否出现硬编码 expected output、占位实现或测试污染。
- **消融 4：禁用回归检查。** 比较当前用例通过率与全套最终通过率之间的差距。
- **参数钩子。** `--max-repair-iterations`、`--build-timeout-seconds`、`--test-timeout-seconds`、`--prompt-budget-chars`、`--use-log-agent` 和 `--log-agent-max-debug-probes` 都可作为实验变量。
- **日志钩子。** 每轮 prompt、LLM JSON、编译错误、测试结果、`runtime.json`、`debug_probe_*.json`、`static_probe_*.json` 可用于构造过程性证据表。

## 局限与反例

- 如果 C 项目本身无法构建，rtest 当前会直接返回空 summary，无法验证 Rust 项目。
- 原始 shell 测试需要人工预处理；如果脚本中仍存在绝对路径、`argv[0]` 差异或环境依赖，agent 只会提示人工审查，不能自动改测试。
- 回归检查只覆盖当前 baseline 中已经通过的用例，不保证对原先失败用例没有副作用。
- `cargo build --release` 可能导致调试信息不足，动态 probe 在某些 release binary 上拿不到完整 locals。
- LLM JSON 协议失败会消耗轮次，虽然有协议反馈，但不等价于修复进展。
- Prompt 材料预算按字符近似管理，不能完全等价于模型 token 预算。

## 可写入论文位置

- **方法章节：功能测试驱动的翻译修复。** 描述从 C shell tests 到 Rust repair loop 的闭环。
- **系统设计章节：证据分层与动作协议。** 用本文件的数据流图说明材料请求、取证和编辑的互斥关系。
- **工程优化章节：只读测试、反作弊和停滞检测。** 作为保证评估有效性的关键机制。
- **实验章节：消融与过程指标。** 对比 LogAgent、回归保护、首轮材料注入、反作弊过滤的影响。
