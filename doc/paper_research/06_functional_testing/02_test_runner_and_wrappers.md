# A4-2 TestRunner 与 wrapper 执行环境调研

## 研究问题

本文调研 `TestRunner` 如何在不改写原始 shell 测试的前提下，让测试脚本调用 Rust 可执行文件，同时保留可选的 C 参考程序。重点问题是：如何映射命令、如何避免污染 `PATH`、如何组织每个用例的运行目录、如何捕获 trace 和超时证据。

核心研究问题如下：

- 原 C 测试脚本中的项目同名命令如何被映射到 Rust binary？
- 为什么 runner 不把 wrapper 目录直接加入 `PATH`？
- 单个测试用例如何获得隔离的临时目录和 fixture？
- 超时、trace、stdout/stderr 等运行证据如何落盘并进入后续修复？

## 流程/数据流

`TestRunner` 的执行流如下：

```text
stage(rust_binary_path, c_binary_path)
    -> 复制 Rust binary 到 test/.bin/<bin>-rust
    -> 复制 Rust binary 到 test/.bin/<bin>
    -> 可选复制 C binary 到 test/.bin/<bin>-c
    -> 扫描派生 alias，如 <bin>_t1
    -> 记录 RUST_BIN / C_BIN / wrapper env

run_single(script)
    -> 为 script 创建 run_root/.run_<stem>
    -> 复制测试 fixture 到 run_dir
    -> 把 wrapper 文件 stage 到 run_dir
    -> 写 .cgr_bash_env
    -> 设置 TMPDIR、srcdir、abs_srcdir、LC_ALL
    -> bash -lc 执行原始脚本
    -> 捕获 stdout/stderr/exit_code/duration
    -> 失败或超时时按需捕获 bash -x trace
    -> 可选写 runtime.json
```

这套机制的目标不是模拟 `cargo test`，而是尽量复用 C 项目原始测试体系。测试脚本仍然认为自己在运行项目同名命令，但该命令通过 `BASH_ENV` 函数被映射到 Rust binary。

## 关键工程细节

- **一次 stage，多次复用。** `stage()` 先把 Rust/C binary 复制到 `test/.bin`，全量测试时复用，只有 rebuild 后才 `restage_rust_binary()`。这减少了每个测试重复复制二进制的成本。
- **同名命令指向 Rust。** 原始 C 测试一般调用 `<bin>`，runner 将 `<bin>` 和 `<bin>-rust` 都映射到 Rust binary；C 参考只通过 `$C_BIN`、`$C_WRAPPER_BIN` 或 `<bin>-c` 暴露。
- **不污染 `PATH`。** 对 `which` 这类项目，`PATH` 本身就是被测行为。runner 不把 `.bin` 注入 `PATH`，而是通过 `BASH_ENV` 定义 bash function 处理直接命令调用。
- **派生 alias 兼容。** 某些测试脚本调用 `yank_t1`、`c4_t2` 一类派生二进制名。runner 扫描脚本并复制额外 wrapper，避免批量重写测试。
- **用例级运行目录。** 每个脚本在 `CGR_RTEST_RUN_ROOT` 或系统临时目录下获得 `.run_<stem>`，避免 Windows 挂载目录不支持 FIFO、socket 等 POSIX 特性。
- **fixture 复制。** 单用例运行前把非 shell fixture 复制进 `run_dir`，脚本可以按相对路径读取输入、期望输出或辅助文件。
- **trace 懒加载。** 首次 `run_all()` 默认不捕获 `bash -x`，失败修复阶段才按需调用 `capture_trace_for()`，减少首跑开销。
- **超时证据完整化。** 超时时会写 `timeout_stdout.txt`、`timeout_stderr.txt`、`timeout_trace.txt`、`timeout_context.txt`，并强制 `TMPDIR` 指向用例目录，方便读取 `mktemp` 产生的中间文件。
- **跨平台路径转换。** `_to_bash_path()` 在 Windows 上优先尝试 `cygpath`，再尝试 `/mnt/<drive>`，最后回退到类 Unix 路径，保证 bash 可执行路径尽量可用。
- **进程组终止。** `_run_bash_with_timeout()` 在 POSIX 上新建 session 并 kill process group，在 Windows 上使用 `taskkill /T`，避免子进程泄漏。

## 可引用代码证据

- `src/agent/rtest/test_runner.py:5`：文件头说明一次 stage、trace 懒加载、显式 cleanup 和不污染 `PATH` 的设计动机。
- `src/agent/rtest/test_runner.py:57`：`TestRunner` 集中管理 staging、运行和 trace 捕获。
- `src/agent/rtest/test_runner.py:80`：`stage()` 一次性 stage Rust/C binary。
- `src/agent/rtest/test_runner.py:86`：Rust binary 复制为 `<bin>-rust`。
- `src/agent/rtest/test_runner.py:92`：Rust binary 同时复制为项目同名 `<bin>`。
- `src/agent/rtest/test_runner.py:101`：C binary 复制为 `<bin>-c`。
- `src/agent/rtest/test_runner.py:109`：扫描并 stage 派生 alias。
- `src/agent/rtest/test_runner.py:116`：设置 `CGR_WRAPPER_DIR`、`RUST_BIN`、`RUST_WRAPPER_BIN` 等环境变量。
- `src/agent/rtest/test_runner.py:160`：`run_single()` 是单用例运行入口。
- `src/agent/rtest/test_runner.py:174`：将 fixture 复制进 `run_dir`。
- `src/agent/rtest/test_runner.py:184`：强制设置 `TMPDIR`。
- `src/agent/rtest/test_runner.py:186`：通过 `BASH_ENV` 注入命令映射。
- `src/agent/rtest/test_runner.py:222`：超时时捕获短 trace。
- `src/agent/rtest/test_runner.py:279`：`write_runtime_log()` 调用 LogAgent 写入运行证据。
- `src/agent/rtest/test_runner.py:322`：超时上下文落盘。
- `src/agent/rtest/test_runner.py:365`：构造 shell 执行命令并导出 `srcdir`、`abs_srcdir`、`LC_ALL`。
- `src/agent/rtest/test_runner.py:397`：写入 `.cgr_bash_env`。
- `src/agent/rtest/test_runner.py:436`：发现派生 Rust alias。
- `src/agent/rtest/test_runner.py:476`：将 wrapper 复制到每个 `run_dir`。
- `src/agent/rtest/test_runner.py:501`：`cleanup()` 清理 wrapper 和运行目录。
- `src/agent/rtest/test_runner.py:574`：超时时杀掉整个进程组。
- `src/agent/rtest/test_runner.py:685`：复制非 shell fixture。

## 实验钩子

- **首跑性能。** 对比首次 `run_all()` 捕获 trace 与不捕获 trace 的总耗时。
- **wrapper stage 成本。** 统计一次 stage 和每用例复制 wrapper 的文件数量、耗时，以及 rebuild 后 `restage_rust_binary()` 的耗时。
- **PATH 污染消融。** 将 `.bin` 注入 `PATH` 与使用 `BASH_ENV` 函数对比，重点观察 `which`、`env`、`command -v` 相关测试。
- **alias 兼容性。** 构造调用 `<bin>_tN` 的测试，比较启用/禁用 `_discover_rust_alias_names()` 的通过率。
- **运行目录策略。** 对比项目目录内 `.run_<stem>` 与系统临时目录，记录 FIFO、长路径、Windows/WSL 兼容问题。
- **超时诊断收益。** 统计有 `timeout_context.txt` 后模型定位 hang 子命令的轮数变化。

## 局限与反例

- `BASH_ENV` 只对 bash 生效；如果测试显式改用其他 shell，函数映射可能失效。
- runner 不注入 `PATH` 是为了减少污染，但如果脚本只通过 `PATH` 查找项目命令，必须由测试预处理或脚本自身环境提供合理路径。
- `_copy_fixtures_into()` 只复制当前脚本目录的非 shell 文件，不递归复制深层 fixture；整体测试目录已预先复制，但每个 `run_dir` 的本地 fixture 仍可能不足。
- 动态 probe 和 static probe 直接运行 binary，不会自动复现完整 shell 脚本前置状态；这一点需要从 trace 中提取 `program_args`。
- Windows、MSYS、WSL 路径转换属于 best effort，极端路径或不存在的挂载点仍可能失败。

## 可写入论文位置

- **系统实现章节：测试环境适配层。** 说明 wrapper、`BASH_ENV` 和 run_dir 隔离机制。
- **工程优化章节：避免测试环境污染。** 用不注入 `PATH` 的设计解释为何评估更接近真实 CLI 语义。
- **实验章节：执行开销与兼容性。** 展示 trace 懒加载、一次 stage、POSIX 临时目录对性能和稳定性的影响。
