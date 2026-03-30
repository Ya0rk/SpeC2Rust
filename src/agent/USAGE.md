# C 到 Rust 项目转换 Agent 使用说明

## 概述

当前框架用于把一个 C 项目转换为 Rust 项目，主流程包括：

1. 分析 C 项目并生成中间文档
2. 根据中间文档生成 Rust 项目代码
3. 对生成结果执行编译修复
4. 对生成结果执行测试修复

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
| `--use-spec-agent` | 使用 `SpecAgent` 作为分析路径 |
| `--use-spec-json-agent` | 在 `SpecAgent` 后增加 JSON 压缩中间层 |
| `--skip-code-fix` | 跳过编译修复步骤 |
| `--skip-test-fix` | 跳过测试修复步骤 |
| `--max-fix-iterations` | 编译修复和测试修复的最大迭代次数，默认 `5` |

## 常见启动方式

### 1. 默认路径

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/
```

对应流程：

`CDocAgent -> RustAgent -> CodeFixer -> TestFixer`

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

### 4. 使用已有分析文档，跳过分析阶段

```bash
python src/agent/main.py --c_project_path ./datasets/avl-tree/ --output_dir ./output/ --skip-c-analysis
```

说明：

- 如果走默认路径，程序会读取 `output/c_docs/final_project_overview.md`
- 如果走 `SpecAgent` 路径，程序会读取 `output/c_docs/docs/rewrite-context/` 和 `output/c_docs/.specify/memory/`
- 如果同时开启 `--use-spec-json-agent`，程序会优先读取 `output/c_docs/spec_json/spec_context.json`

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
  "skeleton_first": true
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

## 当前 Rust 生成策略

`RustAgent` 当前支持骨架优先生成：

1. 先生成文件骨架
2. 再在骨架上补全实现

对于命中 `node/type/data/error` 的文件，骨架阶段会额外强调：

- 优先输出结构体、类型别名、错误枚举
- 优先把字段、类型定义和公开接口写完整
- 优先写类型定义，再写函数签名和实现占位
- 实现阶段尽量保留骨架里已经写出的类型信息，不回退成更空的版本

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
