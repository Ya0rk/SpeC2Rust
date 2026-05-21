# sds 测试用例适配 RustTestAgent 总结

## 1. 测例现状

`datasets/sds`（antirez/sds）的核心交付物是一个名为 `sds-test` 的二进制：`Makefile` 用 `-DSDS_TEST_MAIN` 把 `sds.c` 末尾的 `int main(void)` 编进来，运行时按顺序跑 24 段断言（实际产生的 `test_cond` 子测试更多，因为 `sdsMakeRoomFor` 那段在循环里再产生 ~20 条）。

数据集里给了两份脚本：

- `base_test.sh`：循环跑 `./sds-test "$test_name"`，把退出码当判定。问题是 C 端 `int main(void)` 根本不读 argv，参数传与不传都跑同一套用例，所以"24 个测例"在原脚本里其实是同一份输出被检查 24 次。
- `run_test.sh`：把上面那份按用例展开成 24 个 block，每个 block 调用 `./sds-test_t<N>`（C-instrumented build 多产物命名）+ `LD_PRELOAD=libtracer.so` + 写到 `flow_results/`。同样是把 argv 喂进一个不读 argv 的 main。

**结论**：原脚本对 Rust 端价值不高，需要重新设计。

## 2. 与 RustTestAgent 契约的偏差

| 偏差 | 说明 |
|---|---|
| 没有 `test/` 子目录 | agent 不会发现脚本 |
| 二进制命名 | C 项目目录名是 `sds`，但产物叫 `sds-test`；agent 默认 `bin_name = <目录名>` 时既找不到 C 参考，也会要求 Rust 端有 `sds-rust`。需要显式 `--rust-test-agent-binary-name sds-test` |
| 派生名 `./sds-test_t1` … `_t24` | 之前 avl/c4/yank 的处理已经在 `TestRunner._discover_rust_alias_names` 里支持了，但 sds 这里直接重写成 `./sds-test`，更干净 |
| `LD_PRELOAD=libtracer.so` / `flow_results/` | C-instrumented trace 副作用，Rust 端用不上 |
| argv 反正不被读 | 24 个 case 用同一份输出区分 → 直接复用一次执行结果即可 |

另外发现 agent 旧逻辑里 `_locate_c_binary(c_project_path)` 只用 `Path(c_project_path).name` 找 C 参考，不接 `--rust-test-agent-binary-name` 传过来的名字，对 sds 这种"目录名 ≠ 二进制名"的项目不友好。这次顺手修了。

## 3. 设计

### 3.1 测试拆分策略

`testhelp.h::test_cond` 失败时不退出，只递增 `__failed_tests`；只有最末尾的 `test_report()` 在累计失败 > 0 时 `exit(1)`。所以：

- 跑一次 `./sds-test`，stdout 上能看到 `1 - <descr>: PASSED` / `FAILED` 的逐条记录，以及最后的 `<n> tests, <p> passed, <f> failed` 汇总。
- 给 LLM 修复回路最有价值的输入是「**单个 sub-test 的失败**」而不是「整个聚合输出」。

最终方案：每个 sub-test 一个 `.sh` 文件，每个文件跑一次完整 suite 后用 `grep -qE "^<N> -.*: PASSED$"` 单点判定。失败时 stderr 直接打印整段 suite 输出，给 LLM 足够上下文锁定到具体 sub-test。

### 3.2 文件清单

- `datasets/sds/test/_lib.bash`（共享 helper，刻意不用 `.sh` 后缀避免被 agent 当成测试用例）：
  - `sds_run_suite`：跑 `./sds-test` 一次，缓存到 `$SDS_OUTPUT`。
  - `assert_test_num <N> [<descr>]`：grep 第 N 行的 PASSED；fail 时打印整段输出到 stderr。
- `datasets/sds/test/sds-01-create-and-length.sh` … `sds-23-sdscatrepr.sh`：23 个一行调用 `assert_test_num <N>`。
- `datasets/sds/test/sds-24-sdsmakeroomfor.sh`：除了断言 sub-test #24 PASSED 外，还要求最终汇总行是 `<n> tests, <n> passed, 0 failed$`，把 sdsMakeRoomFor 块里所有内层 `test_cond`（25..N）的失败也抓回来。

### 3.3 性能

每个 `.sh` 都重跑一次完整 suite。suite 含 1MB `sdscatprintf` 分配，单次大概 < 1s。24 个脚本 → 24 次执行，总时延约 20-40s 量级，仍在 `test_timeout_seconds=30s × 24` 的预算内（每个脚本独立计时）。可以接受。

如果将来想优化成"只跑一次 suite，多个 .sh 共享输出"，需要 agent 侧扩出"测试组"概念（共享 fixture cache），现在不必。

## 4. agent 侧改动

`src/agent/rtest/rust_test_agent.py` 在这次任务中做了三处增强：

### 4.1 `_locate_c_binary(c_project_path, bin_name=None)`

接受 `bin_name` 参数，候选顺序为「调用方传入名 → 项目目录名」+ 各自 `.exe` 后缀。这样 `sds-test` 可以被找到当作 `C_BIN`（前提是 `datasets/sds/sds-test` 存在）。

### 4.2 `_locate_release_binary` 兼容 cargo 命名

老逻辑只查 `target/release/<bin_name>-rust[.exe]`。但翻译产物的 `Cargo.toml` 经常把 `[[bin]] name` 设成 C 工具原名（例如 sds 的 `sds-test`，就没有 `-rust` 后缀），导致 cargo 实际产出 `target/release/sds-test`，agent 找不到 release 物件。

新逻辑：

- 候选名 = `[<bin>, <bin> 去掉 -rust 后缀]`，加上 `.exe` 与无后缀两种情况。
- 第一遍只接受通过 magic-byte 校验的原生可执行（ELF / PE-MZ / Mach-O / Wasm），跳过那种 47 字节、`#!/bin/sh` 开头的 LLM 自造 bash wrapper（之前修复轮里残留过）。
- 兜底允许命中非原生 wrapper，保持旧行为。

新增 `_looks_like_native_executable(path)` 模块级 helper 实现 magic-byte 检查。

### 4.3 `_infer_bin_name(c_project_path, rust_project_path)`

调用方没显式传 `--rust-test-agent-binary-name` 时，先读 `<rust>/Cargo.toml`：如果只有一个 `[[bin]] name = "..."`，就用它（剥掉可能的 `-rust` 后缀），否则回落到 C 项目目录名。

这样 sds 这种"目录名 ≠ 二进制名"的场景在不传命令行参数的默认调用下也能跑起来。`run()` 入口被同步更新为：

```python
bin_name = binary_name or self._infer_bin_name(c_project_path, rust_project_path)
```

修改是向后兼容的：所有原本 `bin_name = c_project_path.name` 的项目（cat / avl / c4 / yank / which / head / taskflow 等）的 Cargo.toml 里 `[[bin]] name` 是 `<dir>-rust`，剥掉 `-rust` 后正好等于 `<dir>`，与旧行为一致。

## 5. 验证

- `getDiagnostics`：`rust_test_agent.py` 与 `test_runner.py` 均无诊断。
- `python -m agent.rtest._smoke_test`：全部通过。
- 在现有翻译产物上现地校验解析路径（脚本已删除）：
  - `_infer_bin_name(datasets/sds, output/sds/sds-rust)` → `sds-test`
  - `_locate_release_binary(output/sds/sds-rust, "sds-test-rust")` → `target/release/sds-test`
  - 老 dataset（avl / c4 / cat / head / taskflow / which / yank）的 `bin_name` 仍然是项目目录名，行为未变。
- 一个 `.sh` 内容只有两行（`. _lib.bash` + 一次 assert），保持极小，对 LLM 修复 prompt 友好。

## 6. 使用方式

```
python -m agent.main \
  --c_project_path datasets/sds \
  ... \
  --use-rust-test-agent
```

不需要再显式传 `--rust-test-agent-binary-name sds-test`：agent 会从 `output/sds/sds-rust/Cargo.toml` 的 `[[bin]] name = "sds-test"` 自动识别。

如果 release 产物名既不是 `<bin>-rust` 也不是 `<bin>`，agent 会报 "未找到 Rust 可执行文件" 并跳过；此时仍可显式 `--rust-test-agent-binary-name <name>` 强制指定。

## 7. 改动清单

- `src/agent/rtest/rust_test_agent.py`：
  - `_locate_c_binary` 接受 `bin_name`。
  - `_locate_release_binary` 接受 `<bin>-rust` 与 `<bin>` 双候选 + magic-byte 校验。
  - 新增 `_infer_bin_name` 与 `_looks_like_native_executable` 两个模块级 helper。
  - `run()` 默认使用 `_infer_bin_name`。
- `datasets/sds/test/_lib.bash`：新建。
- `datasets/sds/test/sds-01..sds-24-*.sh`：24 个新建。
- `doc/testcase_investigation/sds.md`：本文档。
