# A8-01：环境隔离与运行封装调研

日期：2026-06-04
责任范围：`scripts/agent.sh`、`scripts/rtest_agent.sh`、`scripts/setup_tcode_linux.sh`、`src/config/config.py`、`src/llm/custom_api.py`、`src/llm/model.py`、`src/utils/round_logger.py`、`src/agent/rtest/*.py`

## 研究问题

本文件回答论文中「系统如何把 C 到 Rust 翻译流程封装成可复跑、低污染的工程运行单元」的问题。这里的环境隔离不是容器级 sandbox，而是由入口脚本、Python 解释器选择、临时目录、API 调用、测试运行目录和日志目录共同形成的轻量隔离层。

核心研究问题包括：

- 主流程如何避免系统 Python、用户 site-packages、`PYTHONPATH` 和 shell 临时目录污染实验结果？
- RustTestAgent 如何把原始 shell 测试运行在 case-local 目录中，而不是直接污染 Rust 项目目录？
- Custom API 调用如何降低代理、限流、长响应和编码异常造成的运行不稳定？
- 当前隔离边界到哪里为止，哪些仍依赖宿主机工具链和数据集约定？

## 流程 / 数据流

### 环境准备

```text
bash scripts/setup_tcode_linux.sh
  -> 选择 CONDA_ENV 或显式 PYTHON
  -> 必要时 CREATE_ENV=1 创建 / 修复 conda env
  -> run_py: PYTHONNOUSERSITE=1 PYTHONPATH= python -I
  -> run_pip: PYTHONNOUSERSITE=1 PYTHONPATH= python -I -m pip
  -> 安装 runtime dependencies
  -> 验证 requests / openai / tree_sitter / tree_sitter_c
```

`setup_tcode_linux.sh` 把依赖安装限定在选定的 conda 环境中，并用 `python -I`、`PYTHONNOUSERSITE=1` 和空 `PYTHONPATH` 降低用户全局包对实验的影响。

### 主入口

```text
bash scripts/agent.sh <project>
  -> WORKSPACE=repo root
  -> DATASET=datasets/<project>
  -> OUTPUT_DIR=output/<project>
  -> TMPDIR=${TMPDIR:-/tmp}/cgrcode-agent
  -> unset PYTHONPATH
  -> find_python()
  -> check_python_env()
  -> python -u ./src/agent/main.py <默认主线 flags>
  -> tee log/agent-<project>-<timestamp>.log
```

主入口默认拒绝系统 Python，除非显式设置 `CGR_ALLOW_SYSTEM_PYTHON=1`。脚本把 `TEMP`、`TMP` 和 `TMPDIR` 指向 `cgrcode-agent`，同时设置 `CONDA_NO_PLUGINS=true`、`PYTHONIOENCODING=utf-8`、`PYTHONUNBUFFERED=1` 和 `PYTHONNOUSERSITE=1`。

### RustTestAgent 旁路

```text
bash scripts/rtest_agent.sh <project>
  -> C_PROJECT=datasets/<project>
  -> RUST_PROJECT=output/<project>/<project>-rust
  -> TMPDIR=${TMPDIR:-/tmp}/cgrcode-rtest
  -> PYTHONPATH=<workspace>/src
  -> python -u -m agent.rtest.rust_test_agent
  -> tee log/rtest-<project>-<timestamp>.log
```

功能测试入口与主入口使用不同临时目录，避免端到端生成和测试修复的临时文件混在一起。它只预检 `requests`，因为测试修复阶段不重新执行 C 静态解析。

### 测试运行隔离

```text
RustTestAgent
  -> CProjectBuilder.clean_and_build()
  -> copy C test/ to Rust project test/
  -> TestRunner.stage()
  -> run_root = ${CGR_RTEST_RUN_ROOT:-tempfile.gettempdir()}/cgrcode-rtest-runs/<bin>-<pid>-<hash>
  -> 每个 .sh case 使用 .run_<case>/tmp
  -> wrapper_dir 映射 Rust binary、C reference binary 和派生命令别名
  -> cleanup() 清理 wrapper 和 run dirs（启用 logging 时保留 run dirs）
```

`TestRunner` 的 case-local run dir 位于系统临时目录，注释说明这是为了避开 Windows-mounted WSL 路径对 FIFO、socket 等 POSIX 文件类型支持不足的问题。测试脚本运行时注入 `TMPDIR`、`srcdir`、`abs_srcdir`、`builddir`、`LC_ALL=C` 和 wrapper 环境变量。

### API 与日志隔离

`Config` 默认 `api_disable_env_proxy=True`，`CustomApiGen` 因此把 `requests.Session.trust_env` 设为 `False`，避免宿主机 `HTTP_PROXY` / `HTTPS_PROXY` 隐式影响直连模型服务。`CustomApiGen` 还做最小请求间隔、限流冷却、指数退避、代理 / TLS 错误识别、无效 Unicode surrogate 清洗和长响应降 `max_tokens` 重试。

`Model.generate()` 统一接入 `RoundLogger`。Round log 默认写入 `log/round_logs/<run>`，可通过 `CGR_ROUND_LOG_DIR`、`CGR_ROUND_LOG_PROJECT` 和 `CGR_ROUND_LOG_RUN` 覆盖。

## 关键工程细节

- **解释器选择是显式策略。** `agent.sh` 优先使用 `PYTHON`、当前 `CONDA_PREFIX`、当前 `VIRTUAL_ENV` 和非系统 `python`，再查找 `CONDA_ENV`。系统 Python 只有显式 opt-in 才进入候选。
- **用户 site 和 `PYTHONPATH` 被默认压低。** 主入口设置 `PYTHONNOUSERSITE=1` 并 `unset PYTHONPATH`；setup 脚本用 `python -I` 安装和验证依赖。
- **临时目录按阶段分离。** 主流程用 `cgrcode-agent`，RTest 旁路用 `cgrcode-rtest`，测试 case 又使用 `cgrcode-rtest-runs/<bin>-<pid>-<hash>`。
- **测试运行目录不在 Rust 项目树下。** 每个测试用例复制 fixture、stage wrapper，并在独立 `tmp/` 下运行，降低 shell 测试对源树和翻译产物的副作用。
- **失败码不被 `tee` 掩盖。** 两个入口脚本在执行 Python 前 `set +e`，运行后读取 `PIPESTATUS[0]` 作为最终退出码。
- **C 项目 contract 很窄。** `CProjectBuilder` 要求 C 根目录有 `Makefile` / `makefile` 和 `test/` 目录，并用 `make clean` + `make` 构建参考 binary。这减少数据集形态不确定性，但也限定了适用范围。
- **API 环境代理默认关闭。** 这对公司代理、系统代理或透明代理环境很重要：默认行为更接近「直连模型 endpoint」。
- **Round log 与主日志分层。** Shell log 捕获整次运行 stdout / stderr；round log 捕获每个 LLM request / reply、调用栈、模型 backend、token usage 和 request options。

## 可引用代码证据

| 论点 | 代码证据 |
| --- | --- |
| 主入口使用 `set -Eeuo pipefail` | `scripts/agent.sh:1-2` |
| 主入口设置独立临时目录和 Python 隔离变量 | `scripts/agent.sh:46-61` |
| 主入口优先选择 conda / venv / 非系统 Python | `scripts/agent.sh:63-129` |
| 主入口预检 `requests`、`tree_sitter`、`tree_sitter_c` | `scripts/agent.sh:132-155` |
| 主入口通过 `tee` 写日志并保留 Python 退出码 | `scripts/agent.sh:204-231` |
| RTest 入口使用独立 `cgrcode-rtest` 临时目录 | `scripts/rtest_agent.sh:63-80` |
| RTest 入口检查 C / Rust 项目存在后运行 module | `scripts/rtest_agent.sh:156-189` |
| setup 脚本选择 / 创建 conda env 并拒绝不可执行 Python | `scripts/setup_tcode_linux.sh:90-136` |
| setup 脚本用 `python -I`、空 `PYTHONPATH` 和 `PYTHONNOUSERSITE=1` 执行安装 / 验证 | `scripts/setup_tcode_linux.sh:138-144` |
| setup 脚本安装和验证 runtime dependencies | `scripts/setup_tcode_linux.sh:167-205` |
| 配置层默认关闭环境代理继承 | `src/config/config.py:1-15`、`src/config/config.py:75-78` |
| Custom API session 默认不信任环境代理 | `src/llm/custom_api.py:46-49` |
| Custom API 做请求节奏控制和退避重试 | `src/llm/custom_api.py:62-81`、`src/llm/custom_api.py:421-475` |
| Custom API 清理 invalid surrogate 并自行序列化 JSON | `src/llm/custom_api.py:292-352` |
| `Model.generate()` 统一记录 round log | `src/llm/model.py:27-47`、`src/llm/model.py:88-103` |
| Round log 支持环境变量指定目录 / run name | `src/utils/round_logger.py:24-43` |
| Round log 使用全局 lock 和 counter 避免文件名冲突 | `src/utils/round_logger.py:310-317` |
| C 项目构建 contract 要求 Makefile 和 `test/` | `src/agent/rtest/c_project_builder.py:1-7`、`src/agent/rtest/c_project_builder.py:40-64` |
| TestRunner run root 位于系统临时目录或 `CGR_RTEST_RUN_ROOT` | `src/agent/rtest/test_runner.py:71-75` |
| 每个 case 使用独立 run dir、fixture、wrapper 和 tmp dir | `src/agent/rtest/test_runner.py:169-188` |
| TestRunner 说明 run dir 不应放在翻译项目树下 | `src/agent/rtest/test_runner.py:268-277` |
| Bash 超时时杀掉进程组 | `src/agent/rtest/test_runner.py:574-623` |

## 实验钩子

| 实验变量 | 控制方式 | 可观察产物 |
| --- | --- | --- |
| Python 隔离强度 | conda env / `PYTHON=` / `CGR_ALLOW_SYSTEM_PYTHON=1` | 启动成功率、依赖失败类型、主日志 |
| 临时目录位置 | `TMPDIR`、`CGR_RTEST_RUN_ROOT` | run dir 结构、shell 测试兼容性、清理残留 |
| API 代理继承 | `api_disable_env_proxy` | 代理 / TLS 错误率、重试次数、响应耗时 |
| API 节流参数 | `api_min_interval_seconds`、`api_max_retries`、`api_rate_limit_cooldown_seconds` | round log duration、失败重试日志 |
| 测试超时策略 | `TEST_TIMEOUT_SECONDS`、`BUILD_TIMEOUT_SECONDS` | timeout artifact、失败签名、进程组清理效果 |
| Round log 目录 | `CGR_ROUND_LOG_DIR`、`CGR_ROUND_LOG_RUN` | 可复跑 run name、每轮调用栈和 token usage |
| RTest logging | `USE_LOG_AGENT=1` | `.cgr_logs`、run dirs 是否保留、runtime evidence |

## 局限与反例

- **不是容器 sandbox。** 脚本没有隔离文件系统、网络、用户权限或 Rust / C 工具链，只是降低 Python、临时目录和测试运行污染。
- **强依赖 Bash / make / cargo。** `setup_tcode_linux.sh` 明确面向 Linux conda；`agent.sh` 和 `rtest_agent.sh` 依赖 Bash 语义。Windows 原生 PowerShell 需要额外入口或兼容层。
- **主入口不预检 Rust 和 C 工具链。** `agent.sh` 只检查 Python 依赖，`cargo`、`make`、C 编译器、`bash`、`lldb` 等下游依赖会在对应阶段暴露。
- **C 项目会被 `make clean` 修改。** `CProjectBuilder.clean_and_build()` 在 C 项目根目录执行 `make clean` 和 `make`，这不是只读 oracle；数据集应视为可重建工作副本。
- **RTest 会复制测试到 Rust 项目 `test/`。** 测试脚本内容只读，但 `test/` 目录本身会被刷新；严格的源树隔离需要额外 snapshot 或外部工作目录。
- **代理关闭不等于网络稳定。** Custom API 仍依赖外部 endpoint、模型服务限流和响应格式，重试只能降低偶发失败，不能保证确定性。
- **Round log 可能包含完整源码和 prompt。** 它提升可观测性，但论文实验需要脱敏或只抽取统计指标。

## 可写入论文位置

- **系统实现章节：实验运行封装。** 说明入口脚本如何把项目名、输出目录、Python runner、临时目录和日志绑定为 run unit。
- **实验设置章节：环境与依赖。** 列出 conda env、Python 依赖、C / Rust 工具链假设、API endpoint 和日志开关。
- **工程优化章节：轻量隔离策略。** 把 Python 环境隔离、RTest case-local run dir、API 代理隔离和 round log 分层作为工程可靠性机制。
- **威胁与局限章节。** 明确系统没有容器级隔离，C oracle 构建会修改数据集工作副本，且跨平台行为仍需单独验证。
