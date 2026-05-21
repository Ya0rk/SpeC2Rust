# wolfcw_libfaketime 测例适配调查报告

调查日期：2026-05-19

## 结论

`wolfcw_libfaketime` 自带测试非常丰富，但它不是普通「编译出一个可执行文件」的项目。核心产物是 `libfaketime.so.1` / `libfaketimeMT.so.1` 这类可通过 `LD_PRELOAD` 注入的动态库，另有 `faketime` wrapper。现有测试主要验证动态链接器插桩、时间相关 libc 函数拦截、共享内存、多进程和 pthread 行为。

因此，它不适合作为当前普通 CLI 翻译项目的主测例。除非我们的 Rust 生成流程支持 `cdylib`、C ABI 导出符号和 `LD_PRELOAD`，否则很难忠实翻译并通过原测试。

适合度：中等，但不适合当前阶段。  
推荐优先级：后续专项。  
是否适合当前「C 可执行文件 + sh 对比 Rust 可执行文件」模型：不适合；更适合「动态库翻译」专项。

## 项目概况

- 项目路径：`datasets/wolfcw_libfaketime`
- 核心源文件：`src/libfaketime.c`
- wrapper 源文件：`src/faketime.c`
- 代码规模：约 6,996 行 C/H 文件
- 构建方式：Makefile
- 核心产物：
  - `src/libfaketime.so.1`
  - `src/libfaketimeMT.so.1`
  - `src/faketime`
- 测试目录：`test/`
- sh 测试数量：约 13 个

## 构建方式

根目录：

```bash
make
```

测试：

```bash
make test
```

根目录 `Makefile` 会进入：

```bash
make -C src all
make -C test all
```

`test/Makefile` 会构建：

- `timetest`
- `shm_layout_test`
- `libmallocintercept.so`
- snippet 测试程序
- constructor 测试程序

## 现有测试结构

主要测试入口：

- `test/test.sh`
- `test/testframe.sh functests`
- `test/test_variable_data.sh`
- `test/test_constructors.sh`

典型测试方式：

```bash
LD_PRELOAD="../src/libfaketime.so.1" FAKETIME="2003-01-01 10:00:05" ./timetest
```

功能测试框架 `test/testframe.sh` 会加载 `test/functests/test_*.sh`，每个测试脚本定义 `run()` 函数，再调用 `run_testcase`。

## 测试覆盖内容

现有测试覆盖：

- `time()`。
- `ftime()`。
- `gettimeofday()`。
- `clock_gettime()`。
- `utime` / `utimensat`。
- pthread 条件变量超时。
- `date` 命令被 `LD_PRELOAD` 影响。
- 绝对时间。
- 相对时间偏移。
- start-at 时间。
- shared memory 跨进程同步。
- constructor 阶段调用时间函数。
- `getrandom` / `getentropy` / `getpid` 等 snippet。
- `FAKETIME_*` 环境变量组合。

测试质量很高，但目标是动态库插桩行为。

## 主要风险

### 核心产物不是普通可执行文件

当前翻译流程如果只生成 `main.rs` 或普通 CLI binary，无法等价替代 `libfaketime.so.1`。Rust 侧需要：

- `crate-type = ["cdylib"]`
- 导出 C ABI 符号。
- 正确实现 libc 函数 interpose。
- 处理 `dlsym(RTLD_NEXT, ...)` 等动态链接逻辑。

这明显超出普通 C2Rust CLI 翻译范围。

### 测试强依赖 `LD_PRELOAD`

测试正确性来自「目标程序加载我们的库后，系统时间调用被拦截」。这不能简单通过比较两个可执行文件输出来完成。

### 存在潜在 hang 风险

README 明确提到某些平台上 `CLOCK_MONOTONIC` 测试可能挂住，需要额外编译宏规避。

### 系统环境影响大

测试受 libc、动态链接器、内核、架构、pthread 行为和 `/dev/shm` 影响。

## 可行的手动改造方向

如果只想借用其中一部分作为普通 CLI 测例，可以测试 `src/faketime` wrapper 的参数解析与错误处理，例如：

```bash
faketime --help
faketime "2000-01-01 00:00:00" date +%Y
```

但这仍然要求 Rust 侧实现动态库加载或提供等效 wrapper 行为，不能代表完整 libfaketime。

如果将它作为专项 benchmark，需要设计新的 Rust 生成目标：

1. 生成 `cdylib`。
2. 生成 `faketime` wrapper binary。
3. sh 测试通过 `LD_PRELOAD` 指向 Rust 动态库。
4. 只选取确定性强的测试，例如 `date +%s`、`time()`、`gettimeofday()`。
5. 对容易 hang 的 pthread/monotonic 测试设置严格 timeout。

## 对 Rust 翻译项目的价值

该项目对研究很有价值，因为它能检验：

- C 动态库到 Rust `cdylib` 的翻译。
- FFI ABI。
- 动态链接器 interposition。
- 全局状态和共享内存。
- 多进程、多线程一致性。

但这不是当前普通可执行文件翻译流程能自然覆盖的内容。

## 最终建议

暂不纳入当前 100 个普通项目评测集。建议单独标记为「动态库 / LD_PRELOAD 专项候选」。如果后续支持 Rust `cdylib` 生成，再从它的 `functests` 中挑选 3 到 5 个确定性强的测试作为第一批专项测例。
