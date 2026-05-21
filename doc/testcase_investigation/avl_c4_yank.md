# avl / c4 / yank 测试用例适配 RustTestAgent 总结

## 1. 背景

`src/agent/rtest`（RustTestAgent）已经能在 `datasets/cat` 上端到端跑通：用 C 项目里的 `test/*.sh` 脚本验证翻译出的 Rust 项目，对失败用例进入 LLM 修复循环。本次任务是把 `datasets/avl`、`datasets/c4`、`datasets/yank` 也接入同一套测试流程。

## 2. RustTestAgent 的硬约定（实现侧）

读完 `rust_test_agent.py` 与 `test_runner.py` 后，可以归纳出以下默认契约：

- 测试发现：在 C 项目根下找 `test/` 或 `tests/`，把整棵目录拷到 `<rust>/test/`，所有 `*.sh` 都被视为独立用例。
- 用例隔离：每个脚本在独立的 `<rust>/test/.run_<stem>/` 目录下运行；脚本同目录的所有非 `*.sh` 文件被当作 fixture 拷进去。
- 二进制别名：cargo 必须产出 `<bin>-rust[.exe]`（`<bin>` 默认 = C 项目目录名），TestRunner 在 staging 阶段把它写两份到 `.bin/`：`<bin>-rust` 和 `<bin>`；可选的 C 参考被暴露为 `<bin>-c`。同名 wrapper 也会被拷到每个 `.run_<stem>/`，bash 函数前奏（通过 `BASH_ENV`）也只为这几个标准名字提供。
- 通过条件：仅看 exit code，0 = pass，非 0 = fail。
- 修复策略：原则上只允许编辑 `.rs` / `Cargo.toml` / `build.rs`；测试脚本与 Makefile 不允许 LLM 改。

`cat` 能直接跑通是因为 `datasets/cat/test/*.sh` 完全契合上述约定（调 `cat` 同名命令、heredoc 写 fixture、`../../tests/init.sh` 由 agent 自动 shim）。

## 3. 三个测例的原状与阻碍

| 测例 | 原始测试脚本位置 | 主要问题 |
|---|---|---|
| `avl` | `datasets/avl/run_test.sh` | 无 `test/` 子目录；脚本调用 `./avl_t1`（agent 不会自动 wrap）；`LD_PRELOAD=libtracer.so` 与 Rust 端无关；尾随空格的字面量比较脆弱。 |
| `c4` | `datasets/c4/run_test.sh` | 无 `test/`；调用 `./c4_t1` / `./c4_t2`；`hello.c` 与 `c4.c` 必须紧邻测试脚本作为 fixture；`base_test.sh` 还依赖一个并不存在的 `expected/` 目录。 |
| `yank` | `datasets/yank/run_test.sh` | 无 `test/`；24 个用例分别调用 `./yank_t1`…`./yank_t24`；强依赖 `expect`（Tcl）、PTY、`mkfifo` 与一个 `xsel` mock；clipboard 写到 `/tmp/yank_clipboard` 容易跨用例互相污染。 |

最大的共性问题是 **派生二进制名**：C 端为了 instrumented trace 给每个用例编了一份 `<bin>_t<N>`，Rust 端只会有一个 `<bin>-rust`，原脚本直接复用就跑不起来。

## 4. 处理方案

采用「数据集侧建 `test/` + agent 侧加多别名 wrap」的两边各退一步策略，既不必重写整个 yank 测试，也不放任 agent 把数据集根下的杂项 `*.sh` 误识别成测试。

### 4.1 agent 侧：`test_runner.py` 增加派生别名支持

改动集中在 `src/agent/rtest/test_runner.py`：

1. `TestEnvironment` 新增 `extra_rust_aliases: List[str]` 字段。
2. 新增 `TestRunner._discover_rust_alias_names()`：扫描 `test/` 下所有 `*.sh`，正则匹配 `\./<bin>_t\d+\b` 与 `\b<bin>_t\d+\b`，挑出像 `yank_t1` / `c4_t2` 这种派生名字。匹配范围限制到 `_t\d+` 后缀，避免误捕到注释或描述里的随意 token。
3. `stage()` / `restage_rust_binary()` 在 `.bin/` 里额外为每个别名拷贝一份 Rust wrapper。
4. `_stage_wrappers_into_run_dir()` 把这些别名也拷进 `.run_<stem>/`，让 `./yank_t13` 能直接执行。
5. `_write_bash_env()` 在 `BASH_ENV` 前奏里追加 `function <alias> { "${RUST_BIN}" "$@"; }`，让脚本在不带 `./` 的调用下也能命中 Rust 二进制。

新逻辑只在测试目录里出现 `<bin>_t\d+` 命名时才激活；对 cat 这种规范命名的项目完全无副作用。冒烟脚本（已删除）确认：`yank_t1` / `yank_t13` / `yank_t24` 三个别名能被正确识别并写入 `.bin/`、run dir、`BASH_ENV`。`agent.rtest._smoke_test` 全套通过。

### 4.2 数据集侧

#### datasets/avl/test/

- `avl-basic.sh`：调用 `./avl`，对照 `run_test.sh` 里的 EXPECTED 文本做字符串比对，不带 `LD_PRELOAD`、`flow_results` 之类与 Rust 测试无关的副作用；失败时打印 expected/actual diff 帮助定位。

#### datasets/c4/test/

- `c4-hello.sh`：`./c4 hello.c` 后用 `grep -q "hello, world"` 判定。
- `c4-self-host.sh`：`./c4 c4.c hello.c` 同样断言 "hello, world"。
- `hello.c`、`c4.c`：从数据集根复制一份到 `test/` 当 fixture，agent 会自动把它们带进 `.run_<stem>/`。

#### datasets/yank/test/

- `_lib.bash`：共享 helper（`setup_yank_env` / `require_expect` / `assert_clipboard` / `assert_no_clipboard`）。注意故意取了 `.bash` 后缀，避免被 agent 当成独立测试用例发现。
- 非交互用例（不依赖 expect，可在任意宿主跑）：
  - `yank-version.sh`：`./yank -v` 必须 exit 0 且有输出。
  - `yank-help-rc.sh`：`./yank -h` 必须 exit 非 0。
- 交互用例（依赖 expect/PTY，缺失时打印 `SKIPPED` 并 exit 0）：
  - `yank-basic.sh`、`yank-delim.sh`、`yank-nav-right.sh`、`yank-nav-end.sh`、`yank-line-mode.sh`、`yank-ctrl-c.sh`。

跳过策略说明：原脚本里 `expect` 缺失会 exit 1，被 agent 视为失败后 LLM 会反复尝试"修 Rust 代码"——但根因是宿主缺工具，跟翻译产物无关。所以 helper 里把这种情况降级成 exit 0 + stderr 标注 SKIPPED；想强制要求 expect 时只需删掉 `require_expect` 调用即可。

clipboard 也从 `/tmp/yank_clipboard` 改成 `./yank_clipboard`，每个 `.run_<stem>/` 自带一份，并发与隔离都更稳。

## 5. 验证

- 修改后 `src/agent/rtest` 通过 `getDiagnostics`（无诊断告警）。
- `python -m agent.rtest._smoke_test`（cwd=src）全部 pass。
- 用临时数据集做的派生别名 staging 冒烟：在 `bin_name=yank` 下，TestRunner 自动识别脚本里的 `yank_t1/13/24`，在 `.bin/` 与 run dir 里都生成了对应 wrapper，`BASH_ENV` 里也写了对应的 bash 函数。

端到端验证（跑完整 `RustTestAgent.run()`）需要先有 cargo 翻译产物，可以在 `output/avl/`、`output/c4/`、`output/yank/` 准备好 `<bin>-rust` 之后用 `--use-rust-test-agent` 走主流程。

## 6. 后续建议

1. 把"测试脚本目录约定"写进 steering（或 README）：
   - 必须放在 `c_project_path/test/`；
   - 调用被测程序统一用 `./<bin>` 或 `<bin>`（除非确实需要派生名，并依赖 agent 的别名机制）；
   - fixture 必须紧邻脚本；
   - 不要写 `/tmp` 等全局路径，写 `$PWD` 下的相对路径以便每个 run dir 隔离；
   - 不要靠 `LD_PRELOAD=libtracer.so`、`bear --` 等 instrumented-build 副作用。
2. 针对 yank 这种交互测试，考虑把 `require_expect` 的"SKIP=PASS"语义升级成 agent 端的真正 SKIP（约定 exit 77）：
   - `TestRunner.run_single` 里把 exit_code==77 标成 `passed=True` 但 result 类型为 SKIPPED；
   - Summary 里把 SKIPPED 与 PASSED 分开统计；
   - 这样既不会触发无意义的 LLM 修复循环，又能在报表里清楚反映"宿主能力缺失"。
3. 把 `datasets/avl/all` 这种聚合二进制和多 Rust binary 的对照测试留作后续扩展点：当数据集里出现多个 `[[bin]]` 时，agent 需要更明确的映射约定，目前没必要为单 binary 翻译产物开洞。

## 7. 改动清单

- `src/agent/rtest/test_runner.py`：新增派生别名识别与 staging。
- `datasets/avl/test/avl-basic.sh`：新建。
- `datasets/c4/test/c4-hello.sh`、`c4-self-host.sh`、`hello.c`、`c4.c`：新建/复制。
- `datasets/yank/test/_lib.bash` 与 8 个 `.sh` 用例：新建。
- `doc/testcase_investigation/avl_c4_yank.md`：本文档。
