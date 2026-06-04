# A4-3 快照、回滚与回归保护策略调研

## 研究问题

本文调研 rtest 修复循环如何把单个失败用例的局部修复约束为套件级稳定改进。核心问题是：LLM 编辑 Rust 项目后，如果当前用例通过但破坏了已通过用例，系统如何发现、回滚并把回归证据反馈给下一轮。

核心研究问题如下：

- 单用例修复前为什么需要项目快照？
- 回滚如何避免项目处于半恢复状态？
- 当前用例通过后如何定义回归集合？
- 回归证据如何进入下一轮 prompt，避免模型只是在不同用例之间来回切换？

## 流程/数据流

套件级修复流程如下：

```text
首次 TestRunSummary
    -> SuiteRepairCoordinator 选择失败用例
    -> baseline_pass_names = 当前已通过用例集合
    -> RustTestAgent._repair_failing_case()
    -> 单用例内创建 ProjectSnapshot
    -> 多轮 edits / build / run_single
    -> 当前用例通过
    -> _check_regression(baseline_pass_names)
    -> 若无回归，接受修复
    -> 若有回归，snapshot.restore()
    -> rebuild + restage wrapper
    -> 重跑当前用例和 baseline
    -> 将 regression_warning 带入下一轮
```

快照恢复流程如下：

```text
ProjectSnapshot.create()
    -> 将 SNAPSHOT_TARGETS 中存在的项复制到同盘临时目录
    -> 任意复制失败则删除快照并抛 SnapshotError

ProjectSnapshot.restore()
    -> 将 live target rename 到 .rtest_trash_<id>
    -> 从快照复制 target 回项目目录
    -> 若复制失败，尽量把 trash rename 回 live
    -> 成功后清理 trash
```

这不是通用版本控制系统，而是围绕 rtest 单用例修复设计的轻量事务边界。

## 关键工程细节

- **快照目标受限。** `SNAPSHOT_TARGETS` 只包含 `src`、`test`、`Cargo.toml`、`Cargo.lock`、`build.rs`，覆盖 LLM 允许编辑的主要项目面，同时不复制 `target/`。
- **快照必须完整。** 创建阶段任何目标复制失败都会抛 `SnapshotError`，避免拿不完整快照做回滚。
- **恢复先 rename，再 copy。** 恢复阶段先把 live target 改名到 `.rtest_trash_*`，再把快照复制回来。失败时尝试把 trash 还原，缩短项目处于不一致状态的窗口。
- **失败用例未修复则回滚。** 单用例最终未修复时，`finally` 中恢复快照，防止未完成 edits 泄漏到后续用例。
- **回归后重建二进制。** 恢复源码后调用 `_touch_rebuild_inputs()` 刷新 `Cargo.toml`、`Cargo.lock`、`build.rs` 和 `src/**/*.rs` 的 mtime，避免 Cargo 复用旧 release binary。
- **wrapper 同步刷新。** 回滚后重新 `cargo build --release` 并 `runner.restage_rust_binary()`，保证测试 wrapper 指向恢复后的 Rust binary。
- **baseline pass 是硬约束。** 当前用例通过后，只检查本轮进入修复前已经通过的用例。若它们失败，则当前 edits 被视为回归并回滚。
- **不切换修复目标。** 回归证据会作为 `regression_warning` 注入下一轮，要求继续修当前失败用例，同时保持被回归用例不坏。
- **套件层会重新跑全量。** 单个 case 修复返回后，`SuiteRepairCoordinator` 仍会 `runner.run_all()`，更新全套 summary 后再选择下一个失败用例。

## 可引用代码证据

- `src/agent/rtest/constants.py:69`：`SNAPSHOT_TARGETS = ("src", "test", "Cargo.toml", "Cargo.lock", "build.rs")`。
- `src/agent/rtest/snapshot.py:31`：`ProjectSnapshot` 对目标子路径做整体快照。
- `src/agent/rtest/snapshot.py:42`：`create()` 在项目同盘创建临时快照目录。
- `src/agent/rtest/snapshot.py:56`：目录目标使用 `shutil.copytree()` 复制。
- `src/agent/rtest/snapshot.py:61`：创建失败时删除快照并抛 `SnapshotError`。
- `src/agent/rtest/snapshot.py:67`：`restore()` 是快照恢复入口。
- `src/agent/rtest/snapshot.py:71`：恢复阶段生成 `.rtest_trash_*` 后缀。
- `src/agent/rtest/snapshot.py:81`：先将 live target `os.rename()` 到 trash。
- `src/agent/rtest/snapshot.py:90`：再从快照复制回原位。
- `src/agent/rtest/snapshot.py:95`：失败时尝试把 trash 改回 live。
- `src/agent/rtest/snapshot.py:120`：`discard()` 清理快照目录。
- `src/agent/rtest/suite_repair_coordinator.py:43`：套件层根据当前 summary 计算 `baseline_pass_names`。
- `src/agent/rtest/suite_repair_coordinator.py:66`：单用例修复后重新 `runner.run_all()`。
- `src/agent/rtest/rust_test_agent.py:1050`：单用例修复前创建 `ProjectSnapshot`。
- `src/agent/rtest/rust_test_agent.py:1103`：未修复时恢复快照。
- `src/agent/rtest/rust_test_agent.py:1114`：`_rebuild_and_restage_after_restore()` 负责回滚后重建。
- `src/agent/rtest/rust_test_agent.py:1138`：刷新输入文件 mtime，避免 Cargo 复用旧二进制。
- `src/agent/rtest/rust_test_agent.py:1997`：当前用例通过后执行 `_check_regression()`。
- `src/agent/rtest/rust_test_agent.py:2009`：发现回归后恢复快照。
- `src/agent/rtest/rust_test_agent.py:2017`：回归回滚后重建并 restage。
- `src/agent/rtest/rust_test_agent.py:2102`：`_check_regression()` 遍历 baseline pass scripts。

## 实验钩子

- **回归率。** 统计每次当前用例通过后，baseline pass cases 中被破坏的比例。
- **回滚成功率。** 记录 `snapshot.restore()` 成功、恢复后 rebuild 成功、恢复后 wrapper 刷新成功三类指标。
- **快照开销。** 记录 `SNAPSHOT_TARGETS` 文件数、总大小、create/restore 耗时。
- **Cargo stale binary 复现实验。** 禁用 `_touch_rebuild_inputs()`，观察恢复源码后是否仍使用旧 release binary。
- **baseline 范围消融。** 比较只检查 baseline pass、检查全套测试、检查最近通过测试三种策略的耗时和漏报率。
- **回归 prompt 效果。** 对比有无 `regression_warning` 时，下一轮是否继续破坏同一 baseline case。

## 局限与反例

- 快照只覆盖 `SNAPSHOT_TARGETS`，如果 LLM 或工具改动了其他文件，回滚不会覆盖。
- 快照复制 `test/` 可能在大型测试目录上带来明显开销，但不复制又可能让只读测试基准被污染。
- 回归检查只覆盖当前 summary 中已经通过的用例，不能保证所有尚未通过用例的行为没有变差。
- `os.rename()` 和复制流程降低了半恢复风险，但不是跨文件系统事务；极端 I/O 失败仍可能留下 trash。
- 并发 agent 同时修改同一 Rust 项目时，快照会把外部修改也纳入或覆盖，当前 rtest 没有跨 agent 锁。
- 如果回滚后当前用例仍通过，系统会接受「回滚已恢复 baseline 且当前用例也通过」的状态；这可能表示之前存在 stale binary 或测试非确定性，需要额外日志分析。

## 可写入论文位置

- **方法章节：回归约束的修复循环。** 用 baseline pass set 描述当前 case 修复的接受条件。
- **系统可靠性章节：轻量事务式快照。** 说明为什么需要在 LLM 编辑前后维护项目一致性。
- **实验章节：回归与回滚分析。** 展示修复成功但引入回归的比例，以及回滚机制对最终套件通过率的影响。
