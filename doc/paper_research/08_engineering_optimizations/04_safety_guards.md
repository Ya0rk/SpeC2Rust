# A8-04：安全守卫与反作弊机制调研

日期：2026-06-04
责任范围：`scripts/agent.sh`、`scripts/rtest_agent.sh`、`src/agent/spec_agent.py`、`src/agent/alternatives/contextual_rust_agent.py`、`src/agent/rust_repair_agent.py`、`src/agent/rtest/*.py`

## 研究问题

本文件研究系统如何降低 LLM 在 C 到 Rust 翻译中「越界生成、测试污染、假实现、路径逃逸、破坏性编辑、回归引入和运行证据污染」的风险。这里的安全守卫不是安全沙箱，而是围绕论文评估有效性和工程可审计性设计的一组边界检查。

核心研究问题包括：

- 生成阶段如何防止 Rust 文件超出 C 项目迁移范围，或泄漏 C ABI 风格实现？
- 编译修复阶段如何防止 LLM 创建路径逃逸文件、空 stub、破坏性替换和未经审计的大范围编辑？
- 功能测试阶段如何保证 shell 测试作为只读 oracle，不被 LLM 改写或硬编码绕过？
- 动态 / 静态 probe 如何采集证据而不污染真实项目？
- 哪些 guard 是执行层强约束，哪些仍是启发式检查？

## 流程 / 数据流

### 生成前后边界

```text
SpecAgent
  -> translation_contract.json
       allowed_rust_files
       dependency_policy
       forbidden_without_evidence
  -> translation_lint.json（只报告）

ContextualRustAgent
  -> 构造 contract-first prompt
  -> 生成文件
  -> _lint_contextual_file()
       contract lint
       C ABI leak lint
       duplicate findings
       reference findings
  -> repair
  -> force-write decision
  -> 拒绝写入或带 reason 强制写入
```

生成阶段的 guard 由 contract、registry 和 lint 组合实现。`SpecAgent` 先把迁移边界结构化；`ContextualRustAgent` 写入文件前检查 contract 和 C ABI 泄漏，发现 fatal finding 会先修复，仍不通过时必须进入 force-write decision。

### 编译修复 guard

```text
RustRepairAgent structured edits
  -> _resolve_project_path()
       拒绝绝对路径、..、.git、target、项目外路径
  -> create_file
       文件类型 allowlist
       不覆盖已有文件（除非 overwrite=true）
       拒绝空内容和 compile-only stub
  -> line edits
       allowed mode
       bracket imbalance check
       destructive edit check
  -> audit_records
  -> cargo check
```

编译修复阶段把 LLM patch 限制为结构化编辑，并在执行前后留下 audit records。它不会盲目应用自由文本 diff。

### 功能测试 guard

```text
RustTestAgent
  -> CProjectBuilder 构建 C 参考程序
  -> copy C test/ 到 Rust project test/
  -> 明确忽略 --translate-tests
  -> 收集 .sh 作为只读基准
  -> ProjectSnapshot.create()
  -> LLM edits
       _filter_disallowed_edits()
       _filter_fake_impl_edits()
       RepairAdapter.apply_structured_edits()
  -> cargo build --release
  -> 当前 case run_single()
  -> _check_regression()
  -> 回归则 snapshot.restore()
```

RTest 的核心安全目标是评估有效性：测试脚本和 fixture 不允许被改，Rust 代码不能直接粘贴 expected output 或占位实现，当前用例修复成功后还要检查已通过用例是否回归。

### Probe guard

```text
debug_probe
  -> LogAgent 必须开启
  -> 不能和 edits / 新材料同轮混用
  -> 必须有有效 breakpoint
  -> 受 max_debug_probes 限制
  -> 证据写入 .cgr_logs

static_probe_update
  -> LogAgent 必须开启
  -> 临时复制 Rust/C project
  -> 拒绝绝对路径和 ..
  -> 插桩只发生在副本
  -> 构建并直接运行 binary
  -> 只收集 [CGR_STATIC:<id>] 行
```

probe 被视为取证动作，而不是编辑动作。静态插桩只应用于临时项目副本，避免污染真实 Rust / C 项目。

## 关键工程细节

- **入口层失败快速暴露。** `set -Eeuo pipefail` 和 `PIPESTATUS[0]` 防止 shell 管道掩盖 Python 失败。
- **文档 lint 是报告型 guard。** `SpecAgent._lint_generated_docs()` 会写 `translation_lint.json`，但不阻断文档生成。
- **C ABI 泄漏 lint 面向 Rust 风格。** ContextualRustAgent 会标记 raw pointer、`unsafe`、`c_void`、`#[repr(C)]`、`extern "C"`、`#[no_mangle]` 等模式，防止初始生成变成 C wrapper。
- **registry 提供重复定义和引用检查。** 文件写入前检查 duplicate findings 和 reference findings，降低多文件生成中的重复 struct / function 和未生成模块引用。
- **RustRepairAgent 拒绝路径逃逸。** 项目路径和 context path 都使用 normalize + `commonpath`，并拒绝 `.git/`、`target/` 和 `..`。
- **防 stub 分两层。** RustRepairAgent 拒绝 compile-only stub；RTest 的 `signals.py` 拒绝 `todo!()`、`unimplemented!()`、占位 panic 和直接硬编码长 expected output。
- **ProjectSnapshot 保护回归。** RTest 在单用例修复前对 `src`、`test`、`Cargo.toml`、`Cargo.lock`、`build.rs` 做快照；未修复或回归时恢复并重新 build / restage。
- **只读测试是执行层约束。** RTest 不仅在 prompt 中要求不改测试，还在 `_is_editable_rust_path()` 拒绝 `test/`、`tests/`、`.sh`、Makefile 和 fixture 类路径。
- **停滞检测防止重复无效修复。** RustRepairAgent 根据错误签名和错误数量窗口判定本轮停滞；RTest 根据测试失败签名连续相同提示模型改变策略。
- **probe 与 edits 互斥。** RTest 执行层会忽略同轮 probe，要求下一轮先看证据，避免证据对应的代码状态不清。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| 主入口失败快速暴露和退出码保留 | `scripts/agent.sh:1-2`、`scripts/agent.sh:204-231` |
| RTest 入口失败快速暴露和退出码保留 | `scripts/rtest_agent.sh:1-2`、`scripts/rtest_agent.sh:204-225` |
| SpecAgent contract 禁止无证据高级能力和依赖 | `src/agent/spec_agent.py:1167-1206` |
| SpecAgent 文档 lint 报告越界阶段、依赖、发布、FFI 和 contract 外文件 | `src/agent/spec_agent.py:1228-1292` |
| ContextualRustAgent static context 明确 contract highest priority 和不扩展能力 | `src/agent/alternatives/contextual_rust_agent.py:1155-1167` |
| ContextualRustAgent 写入前执行 contract / C leak / duplicate / reference lint | `src/agent/alternatives/contextual_rust_agent.py:2118-2131` |
| ContextualRustAgent C ABI leak patterns | `src/agent/alternatives/contextual_rust_agent.py:2142-2158` |
| ContextualRustAgent fatal findings 后 repair / force-write / 拒写 | `src/agent/alternatives/contextual_rust_agent.py:2069-2108` |
| RustRepairAgent 拒绝绝对路径、`..`、`.git/`、`target/` 和项目外路径 | `src/agent/rust_repair_agent.py:929-961` |
| RustRepairAgent context path 同样限制路径逃逸 | `src/agent/rust_repair_agent.py:1077-1117` |
| RustRepairAgent 限制可读 context 文件类型 | `src/agent/rust_repair_agent.py:1171-1179` |
| RustRepairAgent 限制 create_file 类型 | `src/agent/rust_repair_agent.py:1183-1197` |
| RustRepairAgent 检测 compile-only stub | `src/agent/rust_repair_agent.py:1201-1261` |
| RustRepairAgent 检测破坏性已有文件编辑 | `src/agent/rust_repair_agent.py:1265-1305` |
| RustRepairAgent structured edit executor 拒绝非法路径、空文件、stub 和 unsupported mode | `src/agent/rust_repair_agent.py:3276-3421` |
| RustRepairAgent 跳过括号平衡恶化和破坏性编辑 | `src/agent/rust_repair_agent.py:3459-3505` |
| RustRepairAgent 停滞检测 | `src/agent/rust_repair_agent.py:4221-4273` |
| RustTestAgent 明确忽略 `--translate-tests`，测试脚本只读 | `src/agent/rtest/rust_test_agent.py:152-165` |
| RTest 只允许编辑 Rust / Cargo 源文件，拒绝 test、target、shell 和 Makefile | `src/agent/rtest/rust_test_agent.py:607-630` |
| RTest 路径必须在项目根下 | `src/agent/rtest/rust_test_agent.py:632-643` |
| RTest 过滤假实现 edits | `src/agent/rtest/rust_test_agent.py:663-681` |
| `signals.py` 提取 expected output 并检测占位实现 / 硬编码作弊 | `src/agent/rtest/signals.py:140-173` |
| RTest 为失败用例创建 snapshot，未修复时回滚 | `src/agent/rtest/rust_test_agent.py:1049-1112` |
| ProjectSnapshot 完整快照和恢复失败抛错 | `src/agent/rtest/snapshot.py:42-63`、`src/agent/rtest/snapshot.py:67-123` |
| 当前用例通过后检查 baseline pass cases 回归 | `src/agent/rtest/rust_test_agent.py:1996-2074`、`src/agent/rtest/rust_test_agent.py:2102-2116` |
| RTest 对同轮 edits 与 probe 做互斥处理 | `src/agent/rtest/rust_test_agent.py:1325-1350` |
| RTest 限制 debug probe 有断点和次数上限 | `src/agent/rtest/rust_test_agent.py:1386-1403` |
| static probe 复制临时项目副本后插桩 | `src/agent/rtest/runtime_probe.py:244-263` |
| static probe 只收集 `[CGR_STATIC:` 行 | `src/agent/rtest/runtime_probe.py:292-312` |
| static probe 拒绝绝对路径和 `..` | `src/agent/rtest/runtime_probe.py:374-395` |

## 实验钩子

| 实验变量 | 控制方式 | 可观察指标 |
| --- | --- | --- |
| contract lint 开关 | 有无 contract / 有无 write-time lint | 越界文件、无证据依赖、C ABI 泄漏数量 |
| registry guard | 禁用 duplicate / reference findings | 重复定义数、未解析引用数、编译错误前沿 |
| RustRepair edit audit | 禁用 stub / destructive edit check 对照 | 空实现数、文件缩水比例、cargo check 成功但功能失败数 |
| 只读测试 guard | 允许 vs 拒绝测试脚本 edits | 测试污染率、伪通过率 |
| 反作弊过滤 | 禁用 `violates_no_fake_impl()` | expected output 硬编码、todo / panic 占位出现率 |
| snapshot / regression | 禁用回归检查或 snapshot restore | 当前用例通过率与全套通过率差距、回归数 |
| probe 互斥 | 允许同轮 probe + edits 对照 | 证据状态混乱、无效 probe 率、修复轮数 |
| debug probe 上限 | 调整 `--log-agent-max-debug-probes` | 成功率、probe 次数、无效断点和开销 |

## 局限与反例

- **guard 多为启发式。** raw pointer、stub、破坏性编辑和文档越界都是规则匹配，不是语义证明；真实小型适配层可能被误判，复杂作弊也可能漏判。
- **SpecAgent lint 只报告不阻断。** `translation_lint.json` 有助于实验审计，但越界 Markdown 仍可能存在。
- **force-write 是必要逃生口。** ContextualRustAgent 可在模型给出理由后强制写入仍有 finding 的文件，这降低死锁风险，也带来违规内容进入后续阶段的可能。
- **snapshot 覆盖目标有限。** RTest 默认快照 `src`、`test`、`Cargo.toml`、`Cargo.lock`、`build.rs`；其他生成产物、外部缓存或 C 项目变更不在保护范围内。
- **回归检查只覆盖 baseline pass cases。** 它不能证明所有原先失败用例不受影响，也不覆盖未发现的测试。
- **probe 直接运行 binary。** 动态 / 静态 probe 不完整重放 shell 脚本，复杂环境或管道测试可能无法被准确诊断。
- **路径 guard 不等于系统权限隔离。** 结构化编辑限制项目路径，但 shell 测试、make、cargo 和模型 API 仍在宿主环境执行。

## 可写入论文位置

- **工程优化章节：Safety Guardrails。** 组织为生成边界、结构化编辑审计、只读测试 oracle、反作弊、回归保护和 probe 污染控制。
- **方法章节：Closed-loop Repair with Guarded Actions。** 说明 LLM 只能通过受控 action 协议影响项目状态。
- **实验章节：有效性威胁控制。** 报告有无只读测试、反作弊、snapshot 和回归检查时的伪通过 / 回归差异。
- **局限章节：启发式守卫。** 说明这些机制提高工程可靠性，但不能替代容器隔离、形式化验证或完整行为等价证明。
