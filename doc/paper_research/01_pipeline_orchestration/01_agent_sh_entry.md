# A0-01：`agent.sh` 入口编排调研

日期：2026-06-04  
责任范围：`scripts/agent.sh`、`scripts/rtest_agent.sh`、`scripts/run_repair.sh`

## 研究问题

本文件回答论文中「系统如何从一个 C 项目启动完整迁移流程」的问题，重点不是描述一个 shell 脚本，而是说明入口层如何把数据集、输出目录、模型运行环境、默认实验配置和日志产物绑定成可复跑的执行单元。

核心研究问题包括：

- 默认入口是否已经代表论文主线流程，而不是历史 baseline 流程？
- 入口层如何降低 Python 环境污染、临时目录污染和输出路径不一致带来的实验噪声？
- 如何通过环境变量和附加参数支持消融实验、续跑和阶段级复跑？
- `rtest_agent.sh` 与 `run_repair.sh` 作为旁路入口，分别适合验证哪类局部机制？

## 流程 / 数据流

### 完整入口：`scripts/agent.sh`

```text
bash scripts/agent.sh <project-name> [extra main.py args...]
  -> 解析 project-name
  -> DATASET=datasets/<project-name>
  -> OUTPUT_DIR=output/<project-name>
  -> RUST_PROJECT_NAME=<project-name>-rust
  -> LOG_FILE=log/agent-<project-name>-<timestamp>.log
  -> 设置 TEMP/TMP/TMPDIR、Python 隔离变量
  -> find_python
  -> check_python_env
  -> python -u ./src/agent/main.py <默认主线参数> <可选分析参数> <用户追加参数>
  -> stdout/stderr 通过 tee 写入主日志
```

入口层把项目名映射为固定目录约定：C 输入来自 `datasets/<project-name>`，翻译输出写入 `output/<project-name>`，Rust crate 名称默认为 `<project-name>-rust`。这让论文实验可以用项目名作为最小复现实验 ID。

默认主线参数如下：

```text
--use-rust-repair-agent
--use-contextual-rust-agent
--use-rust-test-agent
--use-spec-agent
--use-error-organizer-agent --error-batch-size 10
--rust-entry-kind main
--rust-repair-max-iterations ${CGR_RUST_REPAIR_MAX_ITERATIONS:-64}
--rust-test-agent-max-iterations ${CGR_RUST_TEST_MAX_ITERATIONS:-64}
--rust-test-agent-prompt-budget-chars ${CGR_RUST_TEST_PROMPT_BUDGET_CHARS:-256000}
```

因此，`agent.sh` 当前默认已经不是早期 `CDocAgent -> RustAgent -> CodeFixer -> TestFixer` 链路，而是论文主线：`SpecAgent -> ContextualRustAgent -> RustRepairAgent -> RustTestAgent`，并默认开启错误分批整理。

### 功能测试旁路：`scripts/rtest_agent.sh`

```text
bash scripts/rtest_agent.sh <project-name> [extra RustTestAgent args...]
  或
bash scripts/rtest_agent.sh --c-project-path <path> --rust-project-path <path> [...]
  -> C_PROJECT=datasets/<project-name> 或 C_PROJECT_PATH
  -> RUST_PROJECT=output/<project>/<project>-rust 或 RUST_PROJECT_PATH
  -> python -u -m agent.rtest.rust_test_agent
  -> log/rtest-<project-or-custom>-<timestamp>.log
```

这个入口用于绕过 C 文档生成、Rust 生成和编译修复，只对已有 Rust 项目执行 C 参考行为驱动的功能测试与修复。它适合做 RustTestAgent 的局部消融，例如固定生成结果后比较不同 prompt budget、LogAgent 是否开启、测试超时和 source records 是否提供。

### 编译与测试修复旁路：`scripts/run_repair.sh`

```text
./scripts/run_repair.sh <project_name> [extra RustRepairAgent args...]
  -> 在 output/<project_name> 下查找第一个包含 Cargo.toml 的 Rust 项目
  -> 如果 output/<project_name>/c_docs 存在，则作为修复上下文
  -> python -m agent.rust_repair_agent
  -> 可选 python -m agent.rtest.rust_test_agent
```

这个入口适合在已有生成结果上单独重放编译修复与功能测试修复。它跳过 `main.py`，因此不能作为完整端到端实验入口，但适合论文附录中的阶段级诊断。

## 关键工程细节

- **失败快速暴露。** 三个脚本均使用 `set -Eeuo pipefail`，主入口在真正运行 Python 前临时 `set +e`，通过 `PIPESTATUS[0]` 保留 Python 进程退出码，避免 `tee` 掩盖失败。
- **稳定的目录约定。** `agent.sh` 根据项目名统一构造 `datasets/`、`output/`、`log/` 和 Rust crate 名称，减少实验表格中人工记录路径的负担。
- **临时目录隔离。** `agent.sh` 将 `TEMP/TMP/TMPDIR` 统一指向 `${TMPDIR:-/tmp}/cgrcode-agent`；`rtest_agent.sh` 使用 `${TMPDIR:-/tmp}/cgrcode-rtest`，避免测试和主流程临时文件混在一起。
- **Python 选择优先级。** 主入口优先使用 `PYTHON`、当前 `CONDA_PREFIX`、当前 `VIRTUAL_ENV`、非系统 `python`，再查找 `CONDA_ENV` 指定的 conda 环境。系统 Python 只有在 `CGR_ALLOW_SYSTEM_PYTHON=1` 时才允许兜底。
- **依赖预检。** `agent.sh` 启动前检查 `requests`、`tree_sitter`、`tree_sitter_c`；`rtest_agent.sh` 只检查 `requests`，因为功能测试旁路不需要重新做 C 静态解析。
- **默认主线可一键关闭。** `CGR_NO_DEFAULT_FLAGS=1` 可以禁用默认主线参数，便于跑 legacy baseline 或最小链路。
- **可选证据显式开启。** PointerAgent、MacroAgent 和 LogAgent 不在默认主线中直接开启，分别由 `CGR_USE_POINTER_AGENT`、`CGR_USE_MACRO_AGENT`、`CGR_USE_LOG_AGENT` 控制。这可以把「静态风险证据」和「运行时证据增强」做成清晰消融。
- **用户参数追加在最后。** `main_args` 先放默认参数，再拼接用户传入参数。对于 `argparse` 的重复值型参数，通常后出现的值生效，因此用户可以在命令行覆盖默认迭代次数、入口策略或 prompt budget。
- **日志文件和控制台同源。** `2>&1 | tee "$LOG_FILE"` 保证控制台输出和主日志内容一致；脚本 banner 中打印项目、输入、输出、临时目录和日志路径，方便人工复查。
- **阶段级复跑入口。** `rtest_agent.sh` 和 `run_repair.sh` 允许固定上游生成产物，只替换测试修复或编译修复策略，适合把端到端失败拆成更小的实验。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| 项目名映射到数据集、输出、Rust crate 和日志路径 | `scripts/agent.sh:38-52` |
| 主流程临时目录和 Python 隔离环境 | `scripts/agent.sh:46-61` |
| Python runner 选择规避系统 Python | `scripts/agent.sh:63-129` |
| 入口依赖预检覆盖 `requests`、`tree_sitter`、`tree_sitter_c` | `scripts/agent.sh:132-155` |
| 默认启用 SpecAgent、ContextualRustAgent、RustRepairAgent、RustTestAgent 和 ErrorOrganizerAgent | `scripts/agent.sh:157-170` |
| PointerAgent、MacroAgent、LogAgent 由环境变量控制 | `scripts/agent.sh:171-181` |
| `main.py` 参数由默认参数、可选分析参数和用户参数拼接 | `scripts/agent.sh:184-192` |
| `tee` 写主日志并保留 Python 退出码 | `scripts/agent.sh:204-231` |
| RustTestAgent 旁路支持项目名或显式 C/Rust 路径 | `scripts/rtest_agent.sh:44-62`、`scripts/rtest_agent.sh:166-189` |
| RustTestAgent 旁路提供 source records、verbose 和 LogAgent 开关 | `scripts/rtest_agent.sh:178-187` |
| 直接修复入口会自动查找已有 Cargo 项目 | `scripts/run_repair.sh:38-56` |
| 直接修复入口串联 RustRepairAgent 和 RustTestAgent | `scripts/run_repair.sh:113-137` |

## 实验钩子

可以直接纳入论文实验矩阵的入口层变量：

| 实验变量 | 控制方式 | 可观察产物 |
| --- | --- | --- |
| 主线 vs legacy baseline | `CGR_NO_DEFAULT_FLAGS=1` 后显式选择旧参数 | 主日志、`translation_metrics.json`、编译和测试结果 |
| 编译修复预算 | `CGR_RUST_REPAIR_MAX_ITERATIONS` 或 `--rust-repair-max-iterations` | 修复轮数、最终 `cargo check/build` 状态 |
| 功能修复预算 | `CGR_RUST_TEST_MAX_ITERATIONS` 或 `--rust-test-agent-max-iterations` | 通过用例数、失败用例数 |
| RustTestAgent prompt 预算 | `CGR_RUST_TEST_PROMPT_BUDGET_CHARS` | 功能修复成功率、LLM 轮数 |
| 运行时证据增强 | `CGR_USE_LOG_AGENT=1` | round logs、测试修复日志、动态/静态 probe 记录 |
| 指针/宏辅助证据 | `CGR_USE_POINTER_AGENT=1`、`CGR_USE_MACRO_AGENT=1` | `c_docs` 中辅助文档、修复成功率 |
| 固定生成结果后只跑测试修复 | `scripts/rtest_agent.sh` | `log/rtest-*.log`、RustTestAgent summary |
| 固定生成结果后只跑修复闭环 | `scripts/run_repair.sh` | RustRepairAgent 产物、可选 RustTestAgent 产物 |

建议记录每次运行的命令行、环境变量、`local_config.json` 摘要、git commit、输入数据集版本和输出目录。当前脚本本身尚未自动生成完整 run manifest。

## 局限与反例

- **Bash 入口偏 Linux/Unix。** `agent.sh`、`rtest_agent.sh` 和 `run_repair.sh` 使用 `/tmp`、`/bin/python` 风格路径和 Bash 语义。在 Windows 原生 PowerShell 环境下需要 Git Bash、MSYS2 或 WSL；仓库虽有 `agent.ps1`，但本次 A0 范围未分析它。
- **主入口不检查 Rust 工具链。** `agent.sh` 只检查 Python 依赖，不预检 `cargo`、`make`、C 编译器、`bash` 测试环境和 LLDB。这些失败会在 `main.py` 或下游 agent 中暴露。
- **输出目录不带模型或配置维度。** 默认写入 `output/<project>`，多模型、多配置实验若不改输出目录，容易覆盖历史结果。
- **`run_repair.sh` 的环境隔离弱于 `agent.sh`。** 直接修复入口在找不到 conda 环境时会无条件 fallback 到 `python3` 或 `python`，不像 `agent.sh` 那样需要 `CGR_ALLOW_SYSTEM_PYTHON=1`。
- **`run_repair.sh` 选择第一个 Cargo 项目。** 如果 `output/<project>` 下有多个包含 `Cargo.toml` 的子目录，脚本使用 glob 遍历的第一个项目，实验前需要确认路径。
- **主日志不是结构化数据。** `tee` 日志适合人工审查，但不适合直接做统计。论文实验仍需要从 `translation_metrics.json`、round logs、repair journal 和测试 summary 中抽取结构化指标。
- **用户追加参数覆盖依赖 `argparse` 行为。** 对值型参数通常可覆盖默认值，但布尔开关无法通过后置参数关闭已开启的默认 `store_true`，需要使用 `CGR_NO_DEFAULT_FLAGS=1` 重建参数集合。

## 可写入论文位置

- **方法章节：系统入口与端到端编排。** 用 `agent.sh` 说明论文主线不是单次 LLM 翻译，而是预检、文档化理解、按需生成、编译反馈和行为反馈组成的流水线。
- **实验设置：运行环境与默认参数。** 把默认 flags、迭代预算、prompt budget、日志目录和输出目录约定写成实验复现表。
- **消融实验：阶段开关。** 使用 `CGR_NO_DEFAULT_FLAGS`、LogAgent、PointerAgent、MacroAgent、repair/test iteration 变量构造消融矩阵。
- **附录：复跑命令。** 给出完整入口命令、RustTestAgent-only 命令和 repair-only 命令，帮助读者复现实验或定位失败阶段。
