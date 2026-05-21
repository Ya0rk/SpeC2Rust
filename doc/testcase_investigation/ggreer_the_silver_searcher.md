# ggreer_the_silver_searcher 测例适配调查报告

调查日期：2026-05-19

## 结论

`ggreer_the_silver_searcher` 非常适合做当前翻译项目的测例。它的核心产物是命令行可执行文件 `ag`，现有测试主要是命令输入与 stdout/stderr/exit code 对比，没有网络、挂载、root 权限等重环境依赖。测试数量多，覆盖面广，适合验证 Rust 翻译后的 CLI 行为。

需要注意的是，现有测试是 Cram `.t` 格式，不是普通 sh 文件。我们可以选择安装 `cram` 后直接运行，也可以把高价值 `.t` 测试手动改写成 sh。考虑到我们的 `RustTestAgent` 当前更擅长处理 sh，建议先选取 10 到 20 个 `.t` 文件改造成 sh，用 C/Rust 二进制对比输出。

适合度：高。  
推荐优先级：第一批。  
是否适合当前「C 可执行文件 + sh 对比 Rust 可执行文件」模型：适合。

## 项目概况

- 项目路径：`datasets/ggreer_the_silver_searcher`
- 主程序入口：`src/main.c`
- 代码规模：约 5,440 行 C/H 文件
- 构建方式：Autotools
- 默认产物：`ag`
- 测试目录：`tests/`
- 测试文件数量：约 45 个 `.t` 文件

## 构建方式

项目提供 `build.sh`：

```bash
./build.sh
```

脚本内部执行：

```bash
./autogen.sh
./configure
make -j4
```

主要依赖：

- Automake
- pkg-config
- PCRE
- zlib
- liblzma
- pthread

`Makefile.am` 中的主产物定义为：

```make
bin_PROGRAMS = ag
```

测试目标：

```bash
make test
```

内部执行：

```bash
cram -v tests/*.t
```

## 现有测试结构

测试采用 Cram 格式。例如 `tests/count.t`：

```text
Setup:

  $ . $TESTDIR/setup.sh
  $ unalias ag
  $ alias ag="$TESTDIR/../ag --noaffinity --nocolor --workers=1"
  $ printf "blah\n" > blah.txt

Count matches:

  $ ag --count --parallel blah | sort
  blah.txt:2
```

公共 setup 文件为：

```bash
tests/setup.sh
```

它会把 `ag` alias 到刚构建出的二进制，并强制设置：

- `--noaffinity`
- `--nocolor`
- `--workers=1`
- `--parallel`

这些设置有利于稳定测试输出。

## 测试覆盖内容

现有 `.t` 测试覆盖面非常好，包括：

- 基本搜索。
- 正则匹配。
- 大小写敏感与 `--ignore-case`。
- `--smart-case`。
- 统计匹配数量。
- stdin 搜索。
- exit code。
- 彩色输出。
- column 输出。
- `--vimgrep`。
- only matching。
- passthrough。
- 上下文行。
- ignore 规则。
- `.gitignore` / `.ignore`。
- 隐藏文件。
- 文件类型过滤。
- 二进制文件判断。
- 多行匹配。

这些都是 CLI 翻译项目非常需要的测试类型。

## 主要风险

### 不是普通 sh 格式

`.t` 文件不能直接用 `bash test.t` 执行，需要 `cram`。如果直接复制给当前 `RustTestAgent`，需要先做格式转换。

### 依赖 PCRE 行为

原 C 项目使用 PCRE。Rust 翻译如果改用 Rust `regex` crate，部分 PCRE 特性可能不兼容。测试中复杂正则和 Unicode 行为需要重点关注。

### 压缩文件支持可能增加依赖

项目包含 zlib 和 lzma 支持。若测试覆盖压缩文件搜索，Rust 侧需要对应实现或明确排除该子集。

## 适配建议

### 推荐第一阶段：手写 sh 子集

先从以下稳定 `.t` 转写：

- `count.t`
- `exitcodes.t`
- `case_sensitivity.t`
- `search_stdin.t`
- `files_with_matches.t`
- `only_matching.t`
- `word_regexp.t`
- `literal_word_regexp.t`
- `passthrough.t`
- `vimgrep.t`

每个 sh 做法：

1. 创建临时目录。
2. 准备 fixture 文件。
3. 分别运行 C `ag` 和 Rust `ag`。
4. 固定参数：`--noaffinity --nocolor --workers=1`。
5. 对输出排序或规范化。
6. 比较 stdout、stderr 和 exit code。

### 推荐第二阶段：保留 Cram

如果后续 test agent 能支持 Cram，可以直接运行：

```bash
cram -v tests/*.t
```

但需要把 `tests/setup.sh` 中的 `ag` alias 改为我们的 Rust wrapper，或者通过 `PATH`/函数覆盖 `ag`。

## 对 Rust 翻译项目的价值

该项目非常适合作为模型翻译能力评估，因为它要求 Rust 代码正确处理：

- 文件系统遍历。
- ignore 规则。
- 正则搜索。
- stdout/stderr 格式。
- exit code。
- stdin 与文件输入。
- 多选项组合。

这些行为都能通过 sh 测试稳定复现，失败原因也相对容易定位。

## 最终建议

推荐纳入第一批 benchmark。初期不要全量迁移 45 个 `.t`，先手写一组 10 到 20 个 sh 测试，覆盖核心 CLI 行为。等 `RustTestAgent` 的测试迁移能力更稳定后，再考虑支持 Cram 原格式或自动转换。
