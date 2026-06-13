# C 到 Rust 项目转换 Agent 使用说明

## 概述

当前框架用于把一个 C 项目转换为 Rust 项目，主流程包括：

1. 分析 C 项目并生成中间文档
2. 根据中间文档生成 Rust 项目代码
3. 对生成结果执行编译修复
4. 对生成结果执行测试修复

也支持一个无修复消融流程：

1. 分析 C 项目并生成中间文档
2. 根据中间文档生成 Rust 项目代码
3. 只做编译检查
4. 编译通过后运行项目自带的 `sh` 脚本测试，编译失败直接退出

当前代码支持两类分析路径：

- `CDocAgent` 路径：`CDocAgent -> RustAgent -> CodeFixer -> TestFixer`
- `SpecAgent` 路径：`SpecAgent -> RustAgent -> CodeFixer -> TestFixer`

同时也支持一个可选的 JSON 中间层：

- `SpecAgent -> SpecJsonAgent -> RustAgent -> CodeFixer -> TestFixer`

## 入口命令

当前入口脚本是：

```bash
python src/agent/main.py --c_project_path <C项目路径> --output_dir <输出目录>
```

例如：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/
```

如果使用 `conda` 环境，可以这样运行：

```bash
conda run --no-capture-output -n tcode python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/
```

## 命令行参数

当前主入口支持以下参数：

| 参数 | 说明 |
|------|------|
| `--c_project_path` | C 项目路径 |
| `--output_dir` | 输出目录 |
| `--rust-project-name` | 生成的 Rust 项目名称，默认 `rust_implementation` |
| `--config-file` | 配置文件路径，默认读取仓库根目录的 `local_config.json` |
| `--skip-c-analysis` | 跳过 C 项目分析步骤 |
| `--freeze-c-docs` | 完全禁止产生任何新的 `c_docs` 文件，只复用已有文档 |
| `--use-spec-agent` | 使用 `SpecAgent` 作为分析路径 |
| `--use-spec-json-agent` | 在 `SpecAgent` 后增加 JSON 压缩中间层 |
| `--use-stable-rust-agent` | 使用 `alternatives/stable_rust_agent.py` 作为可选 Rust 生成器 |
| `--use-growth-rust-agent` | 使用 `alternatives/growth_rust_agent.py` 作为可选 Rust 生成器 |
| `--use-pointer-agent` | 可选开启 `PointerAgent`，分析 C 指针并生成 Rust 翻译指导文档 |
| `--use-macro-agent` | 可选开启 `MacroAgent`，分析 C 宏并生成 Rust 迁移指导文档 |
| `--skip-code-fix` | 跳过编译修复步骤 |
| `--skip-test-fix` | 跳过测试修复步骤 |
| `--ablation-no-repair` | 消融模式：禁用所有编译/测试修复 agent，只做编译检查和项目自带 `sh` 测试 |
| `--cargo-conda-env-name` | 执行 `cargo` 命令时使用的 conda 环境名；Windows 消融模式下默认回退到 `c2rust` |
| `--max-fix-iterations` | 编译修复和测试修复的最大迭代次数，默认 `5` |

## 常见启动方式

### 1. 默认路径

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/
```

对应流程：

`CDocAgent -> RustAgent -> CodeFixer -> TestFixer`

说明：

- 当前默认 Rust 生成器是 [`rust_agent.py`](./rust_agent.py)
- 可选替代实现位于 `src/agent/alternatives/`

### 1.1 无修复消融模式

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --ablation-no-repair
```

Windows + conda `c2rust` 环境：

```bash
conda run --no-capture-output -n tcode python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --ablation-no-repair --cargo-conda-env-name c2rust
```

对应流程：

`CDocAgent -> RustAgent -> cargo build --release -> RustTestAgent(shell-only, no repair)`

说明：

- 该模式会禁用 `CodeFixer`、`TestFixer`、`RustRepairAgent`，以及 `RustTestAgent` 的失败修复循环
- 编译通过后会复用 `RustTestAgent` 的 `sh` 测试执行链路，但不会进入任何失败修复循环
- 如果 `cargo build --release` 失败，流程会直接以非零状态退出

### 2. 使用 SpecAgent

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-spec-agent
```

对应流程：

`SpecAgent -> RustAgent -> CodeFixer -> TestFixer`

### 3. 使用 SpecAgent + JSON 中间层

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-spec-agent --use-spec-json-agent
```

对应流程：

`SpecAgent -> SpecJsonAgent -> RustAgent -> CodeFixer -> TestFixer`

### 3.1 使用 StableRustAgent 替代默认 RustAgent

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-stable-rust-agent
```

对应流程：

`CDocAgent -> StableRustAgent -> CodeFixer -> TestFixer`

如果同时使用 `SpecAgent`：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-spec-agent --use-stable-rust-agent
```

### 3.2 使用 GrowthRustAgent 替代默认 RustAgent

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-growth-rust-agent
```

对应流程：

`CDocAgent -> GrowthRustAgent -> CodeFixer -> TestFixer`

如果同时使用 `SpecAgent`：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-spec-agent --use-growth-rust-agent
```

说明：

- `--use-stable-rust-agent` 与 `--use-growth-rust-agent` 互斥
- 不传这两个开关时，主流程默认仍然使用 `RustAgent`

### 3.5 使用 PointerAgent 补充指针翻译指导

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-pointer-agent
```

如果同时和 `SpecAgent` / `SpecJsonAgent` 一起使用：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-spec-agent --use-spec-json-agent --use-pointer-agent
```

如果不使用 `SpecAgent`，`PointerAgent` 会额外扫描所有 `.c/.h` 文件中的指针声明与典型用法，并生成：

- `output/c_docs/pointer_guidance.md`
- `output/c_docs/pointer_guidance.json`

其中 `pointer_guidance.md` 会自动追加到 `RustAgent` 的输入文档中，作为额外翻译指导。

如果同时使用 `SpecAgent`，则指针分析结果会被插入到 `SpecAgent` 内部流程，并为每个模块生成一份简短说明：

- `output/c_docs/specs/<index>-<module>-rust-port/pointer.md`

同时，`SpecAgent` 还会额外生成一份汇总：

- `output/c_docs/docs/rewrite-context/04_gaps_and_risks/001_pointer_macro_summary.md`

当前 `RustAgent` 默认读取的是这份汇总文件，而不是把所有模块下的 `pointer.md` 全部读入。

### 3.6 使用 MacroAgent 补充宏迁移指导

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-macro-agent
```

如果同时和 `SpecAgent` / `SpecJsonAgent` 一起使用：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-spec-agent --use-spec-json-agent --use-macro-agent
```

如果不使用 `SpecAgent`，`MacroAgent` 会额外扫描所有 `.c/.h` 文件中的 `#define` 宏定义，并生成：

- `output/c_docs/macro_guidance.md`
- `output/c_docs/macro_guidance.json`

其中 `macro_guidance.md` 会自动追加到 `RustAgent` 的输入文档中，作为额外的宏迁移指导。

如果同时使用 `SpecAgent`，则宏分析结果会被插入到 `SpecAgent` 内部流程，并为每个模块生成一份简短说明：

- `output/c_docs/specs/<index>-<module>-rust-port/macro.md`

同时，`SpecAgent` 还会额外生成一份汇总：

- `output/c_docs/docs/rewrite-context/04_gaps_and_risks/001_pointer_macro_summary.md`

当前 `RustAgent` 默认读取的是这份汇总文件，而不是把所有模块下的 `macro.md` 全部读入。

### 4. 使用已有分析文档，跳过分析阶段

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --skip-c-analysis
```

说明：

- 如果走默认路径，程序会读取 `output/c_docs/final_project_overview.md`
- 如果走 `SpecAgent` 路径，程序会读取 `output/c_docs/docs/rewrite-context/` 和 `output/c_docs/.specify/memory/`
- 如果同时开启 `--use-spec-json-agent`，程序会优先读取 `output/c_docs/spec_json/spec_context.json`
- 如果同时开启 `SpecAgent + PointerAgent/MacroAgent`，程序还会额外读取 `output/c_docs/docs/rewrite-context/04_gaps_and_risks/001_pointer_macro_summary.md`

注意：

- `--skip-c-analysis` 只跳过主分析步骤
- 如果你同时开启了 `--use-spec-json-agent`、`--use-pointer-agent`、`--use-macro-agent`，这些中间层在默认实现下仍然可能继续生成新的文档

### 4.1 完全冻结 c_docs，只读复用已有文档

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --freeze-c-docs
```

说明：

- 该参数会禁止所有会写入 `output/c_docs/` 的步骤
- 包括主分析步骤、`SpecJsonAgent`、非 `SpecAgent` 路径下的 `PointerAgent` 与 `MacroAgent`
- 程序只会读取现有 `output/c_docs/` 中已经存在的文档
- 如果当前 `output/c_docs/` 不完整或不存在，后续会因为缺少输入文档而报错

### 5. 只生成代码，不做修复

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --skip-code-fix --skip-test-fix
```

## 配置文件

当前版本优先通过配置文件管理模型和 API 参数。

默认配置文件：

- `local_config.json`：本地使用，不提交到 git
- `config.example.json`：示例模板

默认会读取仓库根目录的 `local_config.json`。如果需要，也可以手动指定：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --config-file ./my_config.json
```

## 常用配置项

当前常用配置项如下：

```json
{
  "model_name": "custom_api",
  "api_key": "xxx",
  "api_base_url": "https://your-api/v1",
  "api_model": "your-model",
  "api_max_tokens": 2048,
  "api_min_interval_seconds": 8,
  "api_retry_base_delay_seconds": 12,
  "api_max_retries": 6,
  "api_rate_limit_cooldown_seconds": 90,
  "rag_enabled": false,
  "rag_top_k": 4,
  "generate_tests": false,
  "generate_examples": false,
  "generate_benches": false,
  "skeleton_first": true,
  "round_log_enabled": true,
  "round_log_dir": ""
}
```

各字段含义：

- `model_name`：模型后端名称，例如 `custom_api`、`qwen32`、`oai`
- `api_key`：远程 API 密钥
- `api_base_url`：兼容 OpenAI 接口的基础地址
- `api_model`：远程模型名
- `api_max_tokens`：单次生成允许的最大输出 token 数
- `api_min_interval_seconds`：两次请求之间的最小间隔，避免短时间频繁访问
- `api_retry_base_delay_seconds`：重试基础等待时间
- `api_max_retries`：最大重试次数
- `api_rate_limit_cooldown_seconds`：命中限流后的冷却时间
- `rag_enabled`：是否启用 RAG，默认关闭
- `rag_top_k`：RAG 检索数量
- `generate_tests`：是否生成测试
- `generate_examples`：是否生成示例
- `generate_benches`：是否生成 benchmark
- `skeleton_first`：是否启用骨架优先生成
- `round_log_enabled`：是否记录每一轮 LLM request/reply，默认开启
- `round_log_dir`：round log 输出目录，默认写入 `log/round_logs/<运行时间>-<项目名>/`

## Round Log

底层 `Model.generate()` 会为每一组 LLM 请求和回复写一个独立 JSON 文件，用于观察每一轮对话上下文和生成目标。

默认位置：

```text
log/round_logs/<运行时间>-<项目名>/<轮次>-<目标>.md
```

每个文件包含：

- `objective`：本轮实现目标，优先使用 agent 设置的 request label
- `request`：发送给模型的完整 messages 上下文
- `reply`：模型返回内容
- `error`：如果本轮调用失败，记录异常类型和信息
- `call_stack`：执行 `Model.generate()` 时的 Python 函数栈，便于追踪是哪一个 agent / 函数触发了本轮请求

也可以通过配置改目录或关闭：

```json
{
  "round_log_enabled": true,
  "round_log_dir": "log/round_logs",
  "round_log_project_name": "head"
}
```

或者用环境变量覆盖本次运行目录：

```powershell
$env:CGR_ROUND_LOG_DIR = "E:\\tmp\\cgr-rounds"
$env:CGR_ROUND_LOG_RUN = "quadtree-debug"
```

## 当前 Rust 生成策略

当前默认 `RustAgent` 支持骨架优先生成：

1. 先生成文件骨架
2. 再在骨架上补全实现

对于命中 `node/type/data/error` 的文件，骨架阶段会额外强调：

- 优先输出结构体、类型别名、错误枚举
- 优先把字段、类型定义和公开接口写完整
- 优先写类型定义，再写函数签名和实现占位
- 实现阶段尽量保留骨架里已经写出的类型信息，不回退成更空的版本

当前 Rust 生成器组织方式如下：

- 默认实现：`src/agent/rust_agent.py`
- 可选替代实现：
  - `src/agent/alternatives/stable_rust_agent.py`
  - `src/agent/alternatives/growth_rust_agent.py`

## Spec JSON 中间层

如果使用：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --use-spec-agent --use-spec-json-agent
```

程序会额外执行一个中间步骤：

`SpecAgent -> SpecJsonAgent`

该步骤会把 `SpecAgent` 产出的 markdown 文档压缩成一个机器友好的 JSON 文件：

- `output/c_docs/spec_json/spec_context.json`

这个 JSON 会被直接提供给 `RustAgent`，用于减少冗长自然语言文档对后续生成的干扰。

## 输出目录结构

典型输出结构如下：

```text
output/
├── c_docs/
│   ├── final_project_overview.md
│   ├── docs/
│   │   └── rewrite-context/
│   ├── .specify/
│   │   └── memory/
│   └── spec_json/
│       └── spec_context.json
└── rust_implementation/
    ├── Cargo.toml
    ├── src/
    ├── tests/
    └── ...
```

说明：

- 默认路径主要依赖 `final_project_overview.md`
- `SpecAgent` 路径主要依赖 `docs/rewrite-context/` 和 `.specify/memory/`
- `SpecJsonAgent` 路径会额外产出 `spec_context.json`

## 修复阶段

### 编译修复

编译修复由 `CodeFixer` 执行，默认会进行多轮尝试，直到：

- 编译通过
- 或达到 `--max-fix-iterations`

### 测试修复

测试修复由 `TestFixer` 执行，默认会进行多轮尝试，直到：

- 测试通过
- 或达到 `--max-fix-iterations`

如果当前只关注代码生成，可以先跳过：

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --skip-code-fix --skip-test-fix
```

## 限流型 API 的建议

如果远程 API 不能短时间频繁访问，建议优先调整这些配置：

- `api_max_tokens`：先控制在 `1024 ~ 2048`
- `api_min_interval_seconds`：建议 `8 ~ 15`
- `api_retry_base_delay_seconds`：建议 `10 ~ 20`
- `api_rate_limit_cooldown_seconds`：建议 `60 ~ 120`

当前 `custom_api` 已支持：

- 请求前最小间隔控制
- 指数退避重试
- 命中 `429 / rate limit / too many requests / max retries exceeded` 时的额外冷却
- 响应中断时自动降低 `max_tokens`

## 常见问题

### 1. 为什么我看到 `Max retries exceeded`？

通常说明：

- API 在短时间内被访问过于频繁
- 网络层或代理层不稳定
- 单次请求输出过长，服务端或网关容易中断

建议优先：

- 降低 `api_max_tokens`
- 增大 `api_min_interval_seconds`
- 启用 `SpecJsonAgent`，减少冗长文档输入
- 先关闭测试生成和其他非必要步骤

### 2. 为什么生成过程中经常截断？

常见原因：

- 远程接口对长响应不稳定
- 上下文过长
- 单文件生成任务过大

当前框架已经通过骨架优先策略减少了这类问题，但如果远程服务较脆，仍建议减少单次输出长度。

### 3. 如何查看中间产物？

主要看：

- `output/c_docs/`
- `output/c_docs/docs/rewrite-context/`
- `output/c_docs/.specify/memory/`
- `output/c_docs/spec_json/spec_context.json`
- `output/rust_implementation/`

## 说明

这份文档以当前仓库代码为准，已经去掉了旧版位置参数和 `--model-size` 等过时说明。
