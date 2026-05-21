# opsengine_cpulimit 测例适配调查报告

调查日期：2026-05-19

## 结论

`opsengine_cpulimit` 适合作为中等难度测例。项目规模小，能编译出 CLI 可执行文件 `src/cpulimit`，测试目录也包含 C 测试程序。不过现有 `tests/run_tests.sh` 是空文件，不能直接作为测试入口使用。若允许手动改造，可以写出质量不错的 sh 测试。

它的主要风险是测试依赖 Linux `/proc`、进程枚举、fork、信号、CPU 使用率和时间窗口，因此存在一定不稳定性。建议把它作为「进程控制 / Linux 系统行为」类测例，而不是纯文本 CLI 测例。

适合度：中等偏高。  
推荐优先级：第二批。  
是否适合当前「C 可执行文件 + sh 对比 Rust 可执行文件」模型：适合，但必须手写测试脚本。

## 项目概况

- 项目路径：`datasets/opsengine_cpulimit`
- 主程序入口：`src/cpulimit.c`
- 代码规模：约 1,804 行 C/H 文件
- 构建方式：Makefile
- 默认产物：`src/cpulimit`
- 测试目录：`tests/`
- 测试产物：`tests/process_iterator_test`、`tests/busy`
- 注意：`tests/run_tests.sh` 当前为空文件

## 构建方式

根目录 `Makefile` 会分别进入 `src` 和 `tests`：

```bash
make
```

`src/Makefile` 生成：

```bash
src/cpulimit
```

`tests/Makefile` 生成：

```bash
tests/busy
tests/process_iterator_test
```

主要依赖：

- Linux / POSIX 进程接口。
- pthread（测试中的 `busy` 使用）。
- `/proc`（Linux 进程迭代）。

## 现有测试结构

测试目录包含：

- `busy.c`：创建一个或多个忙等线程，用于制造 CPU 负载。
- `process_iterator_test.c`：C 断言测试，直接测试进程迭代与进程组逻辑。
- `Makefile`：构建上述两个测试程序。
- `run_tests.sh`：空文件。

README 中给出的测试方式是：

```bash
./tests/process_iterator_test
```

因此项目自带测试不是完整 sh 测试套件，需要我们补齐。

## 测试覆盖内容

`process_iterator_test.c` 覆盖：

- 枚举当前进程。
- 枚举当前进程和子进程。
- 枚举所有进程。
- 初始化和更新进程组。
- 处理错误 PID。
- 检查进程名。
- fork 子进程并观察父子关系。
- 基于 CPU 使用率的进程组统计。

这些测试更偏内部模块行为，不直接测试 `cpulimit` CLI 的完整限制效果。

## 主要风险

### 时序和 CPU 使用率不稳定

测试中存在循环采样和 `nanosleep`。在负载高、虚拟机、WSL 或 CI 环境中，CPU 使用率判断可能波动。

### 依赖 Linux 进程模型

该项目核心逻辑依赖 `/proc` 和 POSIX signal。非 Linux 环境不适合。

### 测试目标需要明确

如果只运行 `process_iterator_test`，验证的是内部 C 模块行为；如果要验证 `cpulimit` CLI，需要额外写进程控制脚本。

## 适配建议

### 推荐 sh 测试 1：内部测试程序

构建后直接运行：

```bash
./tests/process_iterator_test
```

该测试适合作为第一层 baseline。Rust 侧如果不生成同名内部测试程序，就需要改写为 Rust 项目暴露等价 CLI 或测试入口。

### 推荐 sh 测试 2：CLI 参数错误

比较 C/Rust 对以下命令的 exit code 和 stderr：

```bash
cpulimit
cpulimit -l 10
cpulimit -l -1 -p 123
cpulimit --help
```

这些测试稳定、低成本。

### 推荐 sh 测试 3：限制 busy 进程

示例思路：

1. 启动 `tests/busy`。
2. 记录 PID。
3. 启动 `cpulimit -p PID -l 20 -z`。
4. 等待短时间。
5. 检查 `cpulimit` 没有异常退出。
6. 结束 busy 进程。

不建议在第一版强行断言精确 CPU 百分比，只建议断言进程存在、可被控制、退出路径正确。

## 对 Rust 翻译项目的价值

该项目能测试：

- Linux `/proc` 解析。
- 进程枚举。
- PID/PPID 处理。
- signal 控制。
- fork/子进程关系。
- CLI 参数解析。
- 长运行进程的测试清理。

这些能力对 C 到 Rust 的系统程序翻译有价值。

## 最终建议

推荐纳入第二批 benchmark，但必须手写 sh。不要依赖空的 `tests/run_tests.sh`。第一版建议只做参数错误、`process_iterator_test` 等稳定测试；后续再增加 CPU 限制行为测试，并为所有后台进程加 cleanup trap。
