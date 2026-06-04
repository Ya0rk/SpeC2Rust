# 数据集与产物库存

## 研究问题

本文件回答「实验对象是什么、哪些产物可以被统计、哪些项目只适合部分指标」的问题。对 C 到 Rust 翻译系统而言，数据集库存不能只列项目名，还要说明 C 输入、shell 测试、构建脚本、Rust 输出、日志和质量计数器之间的对应关系。

核心研究问题包括：

- `datasets/` 中哪些项目可作为端到端翻译样本？
- 哪些项目有 shell 测试，适合纳入功能正确性评估？
- 现有 `output*` 目录是否能直接作为论文实验结果，还是只能作为历史样例？
- 后续批量实验应如何组织 run ID，避免模型、配置和输出目录混淆？

## 指标定义或数据流

默认入口把项目名映射为固定数据流：

```text
datasets/<project>
    -> scripts/agent.sh <project>
    -> output/<project>
    -> output/<project>/c_docs/
    -> output/<project>/<project>-rust/
    -> output/<project>/<project>-rust/repair_journal.jsonl
    -> output/<project>/<project>-rust/translation_metrics.json
    -> log/agent-<project>-<timestamp>.log
    -> log/round_logs/<timestamp>-<project>/*.md
```

功能测试旁路使用同一组输入，但可以固定已有 Rust 项目：

```text
scripts/rtest_agent.sh <project>
    -> C project: datasets/<project>
    -> Rust project: output/<project>/<project>-rust
    -> log/rtest-<project>-<timestamp>.log
```

数据集库存建议使用以下字段：

| 字段 | 含义 | 评估用途 |
| --- | --- | --- |
| `project` | `datasets/` 下的目录名 | 最小实验 ID |
| `c_files` / `h_files` | C 源码和头文件数量 | 规模分层、上下文压力分层 |
| `sh_tests` | `*.sh` 文件数量 | 功能测试覆盖潜力 |
| `makefiles` / `cmake` | 构建入口数量 | C oracle 可构建性预判 |
| `output_root` | `output`、`output_deepseek`、`output_gpt`、`output_gpt2` | 历史运行来源 |
| `has_cargo` | 是否存在 Rust crate | 编译指标可用性 |
| `has_repair_journal` | 是否存在修复日志 | repair 过程指标可用性 |
| `has_translation_metrics` | 是否存在总耗时和 LLM 请求数 | 成本指标可用性 |
| `has_raw_ptr_stats` / `has_unsafe_metrics` | 是否已有质量计数 JSON | 安全性指标可用性 |

本地库存快照（2026-06-04）如下。`sh_tests` 是轻量文件计数，不等于可直接通过的测试用例数。

| project | C | H | sh | Makefile | CMake |
| --- | ---: | ---: | ---: | ---: | ---: |
| `ag` | 12 | 12 | 51 | 1 | 0 |
| `avl` | 1 | 0 | 4 | 1 | 0 |
| `avl-tree` | 4 | 3 | 1 | 0 | 0 |
| `bak` | 11 | 4 | 4 | 3 | 0 |
| `blechschmidt_massdns` | 1 | 14 | 9 | 1 | 1 |
| `c4` | 4 | 0 | 5 | 1 | 0 |
| `cat` | 39 | 84 | 4 | 1 | 0 |
| `cflow-1.8` | 65 | 63 | 4 | 0 | 0 |
| `dvorka_hstr` | 17 | 12 | 34 | 1 | 0 |
| `entr` | 4 | 5 | 1 | 0 | 0 |
| `head` | 38 | 79 | 3 | 1 | 0 |
| `hstr1` | 17 | 12 | 34 | 4 | 0 |
| `jo` | 3 | 2 | 27 | 0 | 0 |
| `kcat` | 8 | 6 | 7 | 2 | 0 |
| `libcsv` | 2 | 1 | 0 | 0 | 0 |
| `libfuse_sshfs` | 4 | 2 | 6 | 0 | 0 |
| `lwext4` | 29 | 33 | 17 | 8 | 3 |
| `opsengine_cpulimit` | 10 | 3 | 1 | 3 | 0 |
| `pwd` | 28 | 73 | 2 | 1 | 0 |
| `quadtree` | 4 | 1 | 0 | 1 | 0 |
| `sds` | 1 | 3 | 27 | 1 | 0 |
| `shc` | 1 | 0 | 11 | 2 | 0 |
| `SipHash` | 2 | 3 | 0 | 1 | 0 |
| `taskflow` | 3 | 1 | 1 | 1 | 0 |
| `tests` | 0 | 0 | 1 | 0 | 0 |
| `urlparser` | 3 | 2 | 0 | 1 | 0 |
| `which` | 6 | 9 | 6 | 2 | 0 |
| `wolfcw_libfaketime` | 23 | 6 | 13 | 4 | 0 |
| `yank` | 1 | 0 | 11 | 1 | 0 |

现有输出目录是历史样例，不是干净的单一实验矩阵：

| output root | 项目目录数 | 备注 |
| --- | ---: | --- |
| `output` | 0 | 默认入口目标，但当前没有项目级样例 |
| `output_deepseek` | 6 | 多数项目有 `Cargo.toml`、`translation_metrics.json` 和 `repair_journal.jsonl` |
| `output_gpt` | 15 | 包含 `bak`、`*_bk` 等备份目录，不能直接当作独立样本 |
| `output_gpt2` | 15 | 包含重复项目和非 `datasets/` 同名项目，如 `ggreer_the_silver_searcher` |

## 关键工程细节

- **项目 ID 由入口脚本定义。** `scripts/agent.sh` 将 `PROJECT_NAME` 映射到 `datasets/<project>` 和 `output/<project>`，因此论文实验应以该映射作为主索引。
- **默认输出目录缺少配置维度。** 多模型、多消融运行如果都写入 `output/<project>`，会覆盖或混合结果。论文实验需要额外 run root，例如 `output_runs/<date>/<model>/<config>/<project>`，或在结果表中记录完整输出路径。
- **功能测试资格不等于 `sh_tests > 0`。** `RustTestAgent` 还要求 C 项目可构建、测试脚本可复制并能通过 wrapper 调用 Rust binary。`libcsv`、`quadtree`、`SipHash`、`urlparser` 等无 shell 测试项目仍可用于编译和代码质量指标。
- **历史产物需要去重。** `output_gpt` 和 `output_gpt2` 中存在 backup、duplicate 和非同名目录。库存表应先映射到 `datasets/<project>`，再判断是否纳入主实验。
- **质量计数器位置不统一。** `raw_ptr_stats.json` 有的写在 Rust crate 下，有的写在项目输出根下；`unsafe_metrics.json` 的历史样例也不完全一致。批处理脚本应递归查找，但结果表要记录统计根目录。
- **round logs 与输出目录分离。** `translation_metrics.json` 在 Rust crate 内，round logs 在 `log/round_logs/<run>/`。成本统计需要按 timestamp、project 或 shell log 反查关联。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| `PROJECT_NAME` 映射到 `datasets/<project>` 和 `output/<project>` | `scripts/agent.sh:41-42` |
| 主日志使用 `log/agent-<project>-<timestamp>.log` | `scripts/agent.sh:52` |
| 默认主线启用 `RustRepairAgent`、`ContextualRustAgent`、`RustTestAgent` 和 `SpecAgent` | `scripts/agent.sh:160-163` |
| 默认 repair/test 迭代预算来自环境变量或 64 | `scripts/agent.sh:167-168` |
| rtest 旁路支持项目名或显式 C/Rust 路径 | `scripts/rtest_agent.sh:55-56`、`scripts/rtest_agent.sh:168-169` |
| rtest 日志使用 `log/rtest-<project>-<timestamp>.log` | `scripts/rtest_agent.sh:71` |
| `RustTestAgent` 复制 C 测试目录到 Rust 项目 `test/` | `src/agent/rtest/rust_test_agent.py:154` |
| `translation_metrics.json` 由主流程在结束时保存 | `src/agent/main.py:785-786` |
| 裸指针计数器跳过 `target/` 并输出 `raw_ptr_stats.json` | `scripts/count_raw_ptrs.py:818`、`scripts/count_raw_ptrs.py:986` |
| `get_unsafe_rate.py` 当前入口把项目根定位到脚本目录下的 `src/<project>` | `scripts/get_unsafe_rate.py:311-316` |

## 实验钩子

- **数据集分层。** 按 `c_files + h_files` 分为小型（<= 5）、中型（6 到 30）和大型（> 30）项目，分别报告编译通过率和功能通过率。
- **测试覆盖分层。** 将 `sh_tests = 0`、`1 到 5`、`> 5` 分层，避免无测试项目拉低或稀释功能测试指标。
- **构建入口分层。** 区分 Makefile、CMake、无显式构建入口，评估 C oracle 构建失败对端到端成功率的影响。
- **历史输出审计。** 对 `output_deepseek`、`output_gpt`、`output_gpt2` 仅做过程样例分析，不作为主表结果，除非补齐 run manifest 和配置记录。
- **质量指标复算。** 对所有最终 Rust crate 统一运行 `count_raw_ptrs.py`，并修正或重写 `unsafe` 计数入口后统一复算 `unsafe_rate`。

## 局限与反例

- 本库存是 2026-06-04 的本地快照，不代表数据集最终版本。
- `*.sh` 文件数只是粗略 proxy。一个 shell 文件可能包含多个 subcase，也可能只是 helper 脚本。
- `tests/` 目录没有 C 源码，应排除出端到端 C 到 Rust 翻译样本，最多作为测试脚本处理样例。
- 历史输出目录没有统一模型、温度、prompt budget、git commit 和环境记录，不能直接用于严格横向比较。
- Windows、MSYS2、Git Bash 和 WSL 的路径语义不同。库存表不应替代实际 C build 和 shell test 预检。
- `get_unsafe_rate.py` 的扫描算法可复用，但当前 CLI 路径像 legacy 脚本，不能直接覆盖 `output*` 下的 Rust crate。

## 可写入论文位置

- **实验设置：数据集表。** 报告项目数量、规模、shell 测试数量和构建入口类型。
- **实验设置：产物与日志约定。** 说明 `datasets/`、`output*/`、`log/`、`repair_journal.jsonl` 和 round logs 的关联方式。
- **威胁与局限：数据集有效性。** 说明 shell 测试数量、历史输出目录和平台差异的限制。
- **附录：项目清单。** 放完整库存表和每个项目是否纳入编译、功能、质量、成本指标。
