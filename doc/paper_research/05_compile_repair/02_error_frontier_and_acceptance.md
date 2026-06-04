# 错误前沿与结果验收机制调研

## 研究问题

编译修复的难点不是每次都减少错误数量。真实 Rust 编译器经常在语法损坏被修好后暴露出更多接口错误，因此「错误数增加」可能是进展。错误前沿（error frontier）机制的研究问题是：如何判断一次修复是否让编译器看到了更深层的问题，并避免把明显退化的候选结果接受为新基线。

## 流程 / 数据流

`RustRepairAgent` 把每次 `cargo check` 或 `cargo build --release` 输出转成 3 类观测值：

- `error_count`：通过正则统计 `error:` / `error[E...]` 行。
- `error_signature`：提取 `error`、`-->`、`could not compile` 等关键行后计算签名。
- `frontier_metrics`：统计语法阻塞模式、接口阻塞模式、总错误数和签名。

`repair_project()` 先建立 baseline，然后每轮拿候选 `RepairRunResult` 调用 `_should_accept_result()`。验收顺序是：

1. 第一个结果直接接受。
2. `cargo check` 从失败变成功，接受。
3. `cargo check` 和 release build 都通过，接受。
4. 语法阻塞数量下降，接受。
5. 语法阻塞清零，接受。
6. 没有语法阻塞时，接口阻塞数量下降，接受。
7. 没有语法阻塞且错误签名变化，同时错误总数没有暴涨超过 80，接受为新的前沿。
8. 否则，如果总错误数下降，接受。
9. 其余情况拒绝，理由为 `did not advance the compile frontier`。

在 copy-runs 模式中，只有接受的候选会推进 `baseline_dir`；在默认原地修复模式中，文件已经被直接修改，验收结果主要用于记录、摘要和后续分析。

## 关键工程细节

- **从错误数量转向错误层级。** `syntax_blockers` 优先级高于 `interface_blockers`。这符合编译器诊断的展开方式：语法损坏会遮蔽类型、模块和借用检查错误。
- **签名变化可代表前沿推进。** 如果语法阻塞已清零，错误签名变化且错误数没有大幅膨胀，也会接受。这允许修复局部错误后看到新的相关诊断。
- **错误签名只保留关键行。** 签名不包含完整 stderr，减少 rustc 帮助文本、note 和上下文变化造成的噪声。
- **交接摘要绑定验收。** 每轮结束后，LLM 会根据基线输出、候选输出、候选摘要和是否接受生成下一轮 handoff summary。被拒绝的轮次要求明确说明「基线未推进」。
- **停滞检测与前沿互补。** 轮内检测连续重复签名和错误数量窗口不改善，退出当前轮；轮间再由 `_should_accept_result()` 判断是否推进基线。

## 可引用代码证据

| 证据点 | 代码位置 |
| --- | --- |
| 统计错误数量 | `src/agent/rust_repair_agent.py:556` |
| 提取错误签名 | `src/agent/rust_repair_agent.py:566` |
| 语法 / 接口阻塞模式和 `frontier_metrics` | `src/agent/rust_repair_agent.py:604` |
| 前沿验收决策顺序 | `src/agent/rust_repair_agent.py:662` |
| 语法阻塞下降优先于错误数 | `src/agent/rust_repair_agent.py:692` |
| 签名变化且错误数未暴涨时接受 | `src/agent/rust_repair_agent.py:720` |
| 轮内停滞检测 | `src/agent/rust_repair_agent.py:4221`、`src/agent/rust_repair_agent.py:4251` |
| `iteration_result` journal 记录验收理由和指标 | `src/agent/rust_repair_agent.py:4465` |
| 单测：语法阻塞移除后即使错误数增加也接受 | `src/tests/test_rust_repair_agent.py:602` |
| 单测：copy-runs 模式拒绝更差候选并保留旧基线 | `src/tests/test_rust_repair_agent.py:1008` |

## 实验钩子

- 从 `repair_journal.jsonl` 中提取 `iteration_result.accepted_as_best`、`accept_reason`、`previous_best_metrics`、`result_metrics`，画出每个项目的前沿推进曲线。
- 对比 2 种验收策略：只按 `error_count` 下降接受；按当前前沿规则接受。观察最终通过率、平均轮数和退化回滚次数。
- 统计 `accept_reason` 分布，区分「语法阻塞清除」、「接口阻塞下降」、「签名变化」、「错误数下降」等进展类型。
- 统计 `post_check.error_signature_after` 与 `error_signature_stall`，度量轮内卡住的比例。
- 在 copy-runs 模式下测试 `apply_best=False/True`，估计前沿保护对最终工程状态的影响。

## 局限与反例

- `interface_blockers` 只是统计模式类型是否出现，不是具体错误实例数；多个 `no method named` 仍只贡献一个模式计数。
- `syntax_patterns` 和 `interface_patterns` 是手写英文字符串，rustc 文案变化、本地化输出或新错误形态会漏判。
- `error_signature` 使用 Python `hash()`，跨进程不稳定。适合单次运行内比较，不适合跨数据集直接聚合。
- 「签名变化 + 错误数不超过 +80」是经验阈值，可能接受无意义 churn；反过来，大规模语法修复后真实暴露大量错误时也可能因为超过阈值被拒绝。
- 默认原地修复无法像 copy-runs 那样真正回退，所以前沿机制在默认路径中不是完整事务。
- release build 通过不等于功能正确，前沿指标只覆盖编译层。

## 可写入论文位置

- **方法章节：** 作为「编译前沿」定义，解释为什么错误数量不是唯一优化目标。
- **实验章节：** 作为消融指标，比较 blocker-aware frontier 与 naive error-count frontier。
- **威胁与局限：** 说明启发式模式、非稳定哈希和原地修复的回滚限制。
