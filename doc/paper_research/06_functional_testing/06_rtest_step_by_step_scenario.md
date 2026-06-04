# rtest 逐步过程详解：以 which 项目为例

1. 用户在仓库根目录 `E:\Code\C2R-Auto\cGrcode` 启动一次翻译与测试流程，例如执行 `bash scripts/agent.sh which --freeze-c-docs --rust-test-agent-max-iterations 3`。这个命令会先完成 C 到 Rust 的翻译、编译修复等前置阶段，只有在主流程允许 `--use-rust-test-agent` 且 Rust 工程已经存在时，才进入 rtest 阶段。

2. 主流程解析参数后确认 `--use-rust-test-agent` 已开启。`scripts/agent.sh` 默认会追加该开关，并把 `--rust-test-agent-max-iterations` 设置为环境变量 `CGR_RUST_TEST_MAX_ITERATIONS` 或默认值 64，把 `--rust-test-agent-prompt-budget-chars` 设置为环境变量 `CGR_RUST_TEST_PROMPT_BUDGET_CHARS` 或默认值 256000。

3. 主流程进入 `src/agent/main.py` 的 `run_optional_rust_test_agent()`。它检查 C 项目路径是否存在，如果没有 `--c_project_path`，rtest 会直接跳过；在 `agent.sh which` 的典型场景中，C 项目路径会解析到类似 `datasets/which` 的目录，Rust 项目路径会解析到本轮生成的 `output/.../which-rust` 工程。

4. `run_optional_rust_test_agent()` 创建 `RustTestAgent`。构造参数包括最大修复轮数、构建超时、单个测试超时、是否启用 LogAgent、最大 debug probe 次数、prompt 材料预算，以及可选的 Rust binary 名称。

5. `RustTestAgent.run()` 将 Rust 项目路径和 C 项目路径都转换成绝对路径。随后它调用内部的 `RustRepairAgent.configure_context_sources(c_project_path=...)`，让后续结构化编辑工具知道 C 源码是只读上下文，而 Rust 工程才是可编辑目标。

6. rtest 推断 `bin_name`。如果用户没有通过 `--rust-test-agent-binary-name` 指定，系统会优先读取 Rust `Cargo.toml` 中唯一的 `[[bin]] name`，如果该名称带 `-rust` 后缀就剥离后缀；否则回退到 C 项目目录名。在 `which` 场景中，`bin_name` 通常是 `which`，Rust release binary 名称则预期为 `which-rust`。

7. rtest 调用 `CProjectBuilder.clean_and_build(c_project_path, expected_bin_name=bin_name)` 构建 C 参考项目。这个阶段先校验 C 项目根目录必须存在 `Makefile` 或 `makefile`，并且根目录下必须存在 `test/` 目录；随后执行 `make clean` 和 `make`，最后在 C 项目根目录定位 C 参考可执行文件。

8. 如果 C 项目不存在、缺少 Makefile、缺少 `test/`、`make` 失败，或者 `make` 成功后找不到根目录可执行文件，rtest 会打印 C 构建 stdout/stderr 尾部，返回 `TestRunSummary(0, 0, 0, [])`，整个功能测试阶段结束。此时不会修改 Rust 代码，因为没有可靠的 C 参考行为和原始测试目录。

9. 如果 C 项目构建成功，rtest 记录 C 参考可执行文件路径，并调用 `RuntimeProbeService.configure_c_target(c_project_path, c_binary)`。这样后续如果启用 LogAgent 或请求 C/Rust 双侧 probe，系统可以同时运行 C reference 和 Rust binary。

10. rtest 使用 C 构建结果中的测试目录。当前 `CProjectBuilder` 的数据集约定要求目录名为 `test/`；在 `which` 场景中，可以理解为从 `datasets/which/test` 复制测试。

11. rtest 将整个 C 测试目录复制到 Rust 工程的 `test/` 目录，例如从 `datasets/which/test` 复制到 `output/.../which-rust/test`。复制的是人工预处理后的原始 shell 测试和 fixture。

12. rtest 调用 `_ensure_test_framework_shim(test_dst)` 准备测试框架 shim。由于复制后的测试目录通常是 `output/<project>/<rust-project>/test`，许多 GNU/coreutils 风格脚本里的 `../../tests/init.sh` 会解析到 `output/<project>/tests/init.sh`，所以 shim 写在 Rust 工程上层的 `tests/init.sh`，用于兼容 `compare`、`skip_`、`returns_`、`require_perl_` 等辅助函数。

13. 如果用户传了 `--rust-test-agent-translate-tests`，rtest 会明确打印忽略该选项。当前策略是不允许 LLM 生成、翻译或改写 `.sh` 测试脚本，测试脚本和 fixture 始终作为只读基准。

14. rtest 调用 `_collect_original_test_scripts(test_dst)` 收集 `test/` 目录下所有原始 `.sh` 测试脚本。它会递归跳过 `.bin`、`.run_*` 和 `.sh.orig`，把脚本换行规范化为 LF，并为每个脚本写出同内容的 `.orig` 备份。如果没有找到任何 `.sh`，rtest 返回空的测试汇总并结束。

15. rtest 在 Rust 工程中执行 `cargo build --release`。这一步验证 Rust 项目至少可以构建出 release 级 CLI 程序，因为 rtest 运行的是外部 shell 测试，不是 `cargo test`。

16. 如果 `cargo build --release` 失败，rtest 打印“无法运行测试”，返回空汇总并结束。此时应由前面的编译修复阶段继续处理，而不是进入功能修复。

17. 如果 Rust release build 成功，rtest 到 `target/release/` 下定位 Rust 可执行文件。对于 `which`，期望路径类似 `target/release/which-rust` 或 Windows 下的 `target/release/which-rust.exe`。

18. 如果找不到 Rust 可执行文件，rtest 会提示检查 `Cargo.toml` 中是否有 `[[bin]] name = "which-rust"`，然后返回空汇总。

19. rtest 创建 `TestRunner(test_dir=test_dst, bin_name=bin_name, timeout_seconds=..., enable_logging=...)`。`TestRunner` 负责 staging wrapper、运行 shell 脚本、捕获 stdout/stderr、按需捕获 bash trace，并在结束时清理临时目录。

20. `TestRunner.stage(rust_binary_path, c_binary_path)` 创建 `test/.bin` wrapper 目录，并把 Rust release binary 复制成两个名字：`which-rust` 和 `which`。原始 C 测试脚本通常直接调用项目同名命令，所以这里让 `which` 指向 Rust binary。

21. `TestRunner.stage()` 同时把 C reference binary 复制成 `which-c`，并设置环境变量 `C_BIN`、`C_WRAPPER_BIN` 等。C reference 不会替代原始 `which` 命令，它只通过 `which-c` 或环境变量暴露给取证和对照场景。

22. `TestRunner.stage()` 扫描所有 `.sh` 测试脚本，寻找类似 `./which_t1`、`which_t2` 这样的派生二进制名。如果发现这些名字，就额外复制 wrapper，并在后续 `BASH_ENV` 中定义同名 bash function，让这些派生命令也指向 Rust binary。

23. `TestRunner.stage()` 设置基础环境变量，包括 `CGR_WRAPPER_DIR`、`RUST_BIN`、`RUST_WRAPPER_BIN`、`RUST_NAMED_WRAPPER_BIN`、`C_BIN`、`LC_ALL=C` 等。它不会把 wrapper 目录注入 `PATH`，因为 `which` 这类项目对 `PATH` 极其敏感，污染 `PATH` 会改变被测程序行为。

24. rtest 调用 `runner.run_all(scripts)` 执行首次全量测试。首次全量测试默认不捕获 `bash -x` trace，只收集每个脚本的 exit code、stdout/stderr 尾部和耗时，以降低首跑成本。

25. `run_all()` 对每个脚本调用 `run_single(script, capture_trace=False)`。例如它依次运行 `test-basic.sh`、`test-options.sh`、`test-path.sh` 等脚本，并为每个结果打印通过或失败标记。

26. `run_single()` 为当前脚本创建隔离运行目录。运行目录不放在项目树下，而是在临时目录的 `cgrcode-rtest-runs/<bin>-<pid>-<hash>/.run_<script_stem>` 下，以避免 Windows 挂载路径或项目目录对 FIFO、socket、临时文件造成限制。

27. `run_single()` 把测试脚本所在目录的 fixture 复制到本次运行目录，再把 `test/.bin` 中的 Rust/C wrapper stage 到运行目录。这样每个测试都在自己的 scratch 目录中运行，不互相污染。

28. `run_single()` 写入 `.cgr_bash_env`。这个文件通过 bash 的 `BASH_ENV` 自动加载，定义 `which()`、`which-rust()`、`which-c()` 以及派生 alias 的 bash function。原始脚本调用 `which` 时实际运行 Rust binary，调用 `which-c` 时才运行 C reference。

29. `run_single()` 设置 `TMPDIR` 为运行目录下的 `tmp/`，并导出 `srcdir=.`, `abs_srcdir=<run_dir>`, `builddir=.`, `abs_builddir=<run_dir>`, `top_srcdir=.`, `abs_top_srcdir=<run_dir>`。这些变量用于兼容 GNU/coreutils 风格 shell 测试。

30. `run_single()` 通过 `bash -lc` 执行测试脚本。非 trace 模式下命令形式等价于先导出 wrapper 和目录变量，再运行 `bash <script>`。

31. 如果测试进程正常退出且 exit code 为 0，该脚本标记为通过。rtest 保存 stdout/stderr 尾部、耗时、运行目录和通过状态。

32. 如果测试进程非 0 退出，该脚本标记为失败。首次全量测试阶段只保存 stdout/stderr 尾部，不立即捕获 trace。

33. 如果测试超时，`run_single()` 把 exit code 设为 -1，保存 timeout stdout/stderr，并用较短超时再捕获一次 trace。它还会写入 `timeout_stdout.txt`、`timeout_stderr.txt`、`timeout_trace.txt` 和 `timeout_context.txt`，方便下一轮 prompt 精确说明卡住位置。

34. 如果启用了 LogAgent，`run_single()` 会把当前测试结果压缩成运行时日志，写入运行目录下的 `.cgr_logs/runtime.json`。这个文件后续可以作为 runtime evidence 注入 prompt。

35. 首次 `run_all()` 完成后，rtest 打印“首次测试结果”。如果所有测试都通过，rtest 直接返回汇总，最后由 `runner.cleanup()` 清理 wrapper 和运行目录。

36. 如果存在失败测试，rtest 加载 Rust 项目结构摘要。主路径会优先读取生成阶段留下的 `.cgr_generation_plan.json` 中的 `project_structure`；如果没有该文件，再回退到扫描 Rust 项目结构。这个摘要用于让 LLM 看到 Rust 工程的模块分布。

37. rtest 调用 `load_source_records(c_project_path, explicit_path=...)` 加载 C 源码索引。如果存在类似 `src/parse/res/which.json` 的源记录文件，会优先使用；否则会从 C 项目中扫描源码。索引用于后续按 flag、关键字或函数名注入 C 对照片段。

38. rtest 创建 `SuiteRepairCoordinator`。套件协调器拿到 Rust 项目路径、`bin_name`、`runner`、项目结构、C source index、首次测试汇总、脚本列表、初始 Rust binary 路径和最大套件修复轮数。

39. `SuiteRepairCoordinator.run()` 从当前 summary 中选择一个当前要修复的脚本。它会跳过本轮已经尝试过的失败用例，并按脚本文件大小从小到大排序，优先修较小的失败脚本。假设 `which` 的 `test-path.sh` 失败，它会把这个 `TestCaseResult` 交给 `RustTestAgent._repair_failure()`。

40. `_repair_failure()` 打印当前失败用例名，例如 `[rtest] --- 修复失败用例：test-path.sh ---`。

41. 如果失败用例还没有 trace，`_repair_failure()` 调用 `runner.capture_trace_for(script_path)` 懒加载 `bash -x` trace。此时只对当前失败脚本捕获 trace，不重新给所有脚本捕获。

42. `capture_trace_for()` 复用或创建当前脚本的运行目录，确保 fixture、wrapper、`.cgr_bash_env` 和 `tmp/` 都已准备好，然后用 `bash -x <script>` 重新运行脚本。

43. trace 只保留尾部，例如 `TRACE_TAIL_CHARS` 或 prompt 专用的 `PROMPT_TRACE_TAIL_CHARS` 范围内的内容。这样 prompt 中能看到最后失败的 shell 分支、命令参数和比较逻辑，而不会被完整长脚本淹没。

44. `_repair_failure()` 调用 `extract_test_flags(script_text)` 从测试脚本中推断被测 flag，例如 `-a`、`-s`、`--skip-dot` 或其他 `which` 选项。

45. `_repair_failure()` 调用 `extract_test_keywords(script_text)` 提取测试关键字，例如 `PATH`、`alias`、`not found`、`executable`、`tilde` 等。关键字用于从 C/Rust 源码中检索相关片段。

46. `_repair_failure()` 调用 `extract_expected_outputs(script_text)` 从 heredoc 或比较语句里提取期望输出。较长 fixture 会被阈值过滤，避免把大文件内容当成硬编码目标。

47. `_repair_failure()` 创建 `MaterialBudget(budget_chars=prompt_budget_chars)`。默认预算是 256000 字符，管理 C 记录、Rust 文件和测试产物三类材料。

48. `_repair_failure()` 调用 `seed_c_sources()`，根据 flag、关键字和 C source index 选择少量高相关 C 源码片段注入首轮 prompt。默认上限由 `SEED_C_LIMIT` 控制，例如最多注入 4 条 C 源记录。

49. `_repair_failure()` 调用 `seed_rust_files()`，根据项目结构和关键字选择高相关 Rust 文件注入首轮 prompt。默认上限由 `SEED_RUST_LIMIT` 控制，例如优先注入 `src/main.rs`、`src/lib.rs` 或包含 `which` 选项解析逻辑的模块。

50. `_repair_failure()` 把当前测试脚本、失败输出、timeout context、可见产物摘要等作为 test artifact 注入材料预算。它还会从当前 run dir 中挑选较小且相关的测试产物，例如 `.out`、`.err`、`.log`、`.stderr`、`.stdout`、`*.x.c` 等。这样模型可以读取测试的真实判定逻辑，但仍不能编辑测试文件。

51. `_repair_failure()` 创建 `ProjectSnapshot(rust_project_path)`。快照目标通常包括 `src/`、`test/`、`Cargo.toml`、`Cargo.lock`、`build.rs`。虽然测试文件只读，快照仍用于在回归时恢复 Rust 工程状态。

52. rtest 初始化当前用例修复状态，包括 `history_summary`、`last_failure_signature`、`stall_count`、`build_error`、`regression_warning`、`debug_probe_count` 和 `static_probes`。

53. rtest 进入当前失败用例的第 1 轮 LLM 修复，打印 `修复迭代 1/N`。

54. `_run_repair_attempt()` 调用 `build_repair_prompt()` 生成 prompt。prompt 中包含项目结构、当前失败脚本、stdout/stderr、trace 尾部、推断出的 flag、关键字、expected output、当前材料表、材料预算压力、历史摘要和动作 JSON 协议。

55. 如果启用了 LogAgent，`build_repair_prompt()` 会读取当前运行目录下的 runtime evidence，并把压缩后的 `runtime.json` 注入 prompt。若之前执行过 debug probe 或 static probe，也会把最新 probe 证据作为 instrumentation context 注入。

56. prompt 明确告诉模型：不能编辑测试脚本、fixture 或 C 项目；不能硬编码 expected output；不能用 `todo!()`、`unimplemented!()`、占位 `panic!()` 伪造实现；编辑必须使用真实行号；每轮可以请求更多材料、请求取证或提交 Rust 编辑。

57. LLM 必须返回 JSON。典型字段包括 `summary`、`edits`、`cgr_read`、`rust_read`、`test_artifact_read`、`material_keep`、`debug_probe`、`static_probe_update` 和 `complete`。

58. `RepairResponseContract` 解析 LLM 输出。如果 JSON 无效、字段类型不对或编辑协议不符合要求，rtest 会把协议错误写入 `history_summary`，进入下一轮要求模型修正输出格式。

59. rtest 读取 `material_keep`，但它只把该字段作为优先级提示，不会立即硬删除材料。真正的材料淘汰由 `MaterialBudget` 在超过字符预算时按插入顺序进行 LRU 式淘汰。

60. rtest 检查 `edits` 的路径。只有 Rust 工程内的 `.rs`、`Cargo.toml`、`Cargo.lock`、`build.rs` 等可编辑文件被接受；指向 `test/`、`target/`、wrapper、shell 脚本、C 源码或项目外路径的编辑会被拒绝。

61. rtest 调用反作弊检测，拒绝疑似假实现。典型拒绝对象包括 `todo!()`、`unimplemented!()`、占位 panic、直接把长 expected output 字符串写入 Rust 源码等。

62. 如果 LLM 同一轮既请求新的 test artifact 又提交 edits，rtest 会优先提供材料并跳过 edits。原因是测试产物可能改变对失败原因的理解，先编辑容易基于不完整证据做错。

63. 如果 LLM 请求 `cgr_read`，rtest 根据 C source index 提供对应 C 源码记录、文件片段或整文件。小文件或多片段请求可能升级为 whole file，以减少碎片化上下文。

64. 如果 LLM 请求 `rust_read`，rtest 检查路径是否在 Rust 项目内且属于允许读取的源码文件。行范围会按实际文件长度修正，重复行范围会跳过，小文件或多片段请求也可能升级为 whole file。

65. 如果 LLM 请求 `test_artifact_read`，rtest 从 `test/`、当前运行目录、timeout artifacts 或日志产物中读取对应文件。读取结果作为 test artifact 加入 `MaterialBudget`，但不会给模型任何编辑测试的权限。

66. 如果这一轮新增了任何材料，rtest 会打印新增材料清单，把材料加入下一轮 prompt，并通常跳过本轮编辑或取证。下一轮模型会在更完整证据下重新判断。

67. 如果 LLM 同一轮既有 edits 又有 `debug_probe`，rtest 会忽略 `debug_probe`。debug probe 是纯取证动作，不能和编辑混在同一轮，否则证据到底对应修改前还是修改后会不清楚。

68. 如果 LLM 同一轮既有 edits 又有 `static_probe_update`，rtest 会忽略 `static_probe_update`。static probe 也属于取证配置更新，应当在无编辑轮单独执行。

69. 如果 LLM 请求 `static_probe_update` 且本轮没有 edits 和新增材料，rtest 解析 probe 配置。动作可以是添加、替换、删除或清空某些静态 probe。

70. rtest 将 static probe 注入临时项目副本或目标文件，执行构建与测试观察，把结果写入 `static_probe_<attempt>.json`。下一轮 prompt 会把这些观测值作为证据，而不是立刻修改生产源码。

71. 如果 LLM 请求 `debug_probe` 且本轮没有 edits 和新增材料，rtest 检查 probe 是否有有效断点。无断点的 probe 会被跳过，并把错误反馈写入历史。

72. 如果 debug probe 有效且未超过本用例 probe 次数上限，rtest 调用 `RuntimeProbeService.execute_debug_probe()`。它可以对 Rust、C 或两侧目标设置断点、运行当前测试场景，并把局部变量、调用栈或输出摘要写入 `debug_probe_<attempt>.json`。

73. debug probe 执行后，rtest 不把它当成修复成功。它只把 probe 结果注入下一轮 prompt，让模型基于动态证据再决定是否编辑。

74. 如果 LLM 提交了有效 edits，rtest 通过 `RepairAdapter` 复用 `RustRepairAgent` 的结构化编辑能力应用修改。编辑可以是行范围替换、插入、删除或创建允许的 Rust 项目文件。

75. rtest 记录应用结果，例如 `applied=True, edits=2`。如果没有编辑被实际应用，会把原因写入历史，要求下一轮改变策略或请求材料。

76. 编辑应用后，rtest 刷新被编辑 Rust 文件的材料。这样下一轮 prompt 中的行号和源码内容对应最新版本，而不是旧材料。

77. `_build_and_verify()` 开始验证编辑结果。第一步是在 Rust 工程中重新执行 `cargo build --release`。

78. 如果 rebuild 失败，rtest 把 cargo build 错误尾部写入 `state.build_error`，刷新相关 Rust 文件材料，然后进入下一轮。下一轮 prompt 会要求模型先修复这个构建失败。

79. 如果 rebuild 成功但 release binary 缺失，rtest 把“编译产物缺失”写入历史，跳过本轮测试验证，进入下一轮。

80. 如果 rebuild 成功且 binary 存在，rtest 调用 `runner.restage_rust_binary(new_binary_path)` 更新 `test/.bin` 和运行目录中的 Rust wrapper。C reference wrapper 保持不变。

81. rtest 调用 `runner.run_single(failing_case.script_path, capture_trace=True)` 重跑当前失败用例。这一次会捕获 trace，因为它用于判断当前修复是否真正改变失败路径。

82. rtest 打印当前用例结果，例如 `当前用例结果：FAIL, exit=1` 或 `当前用例结果：PASS, exit=0`，并把新的 stdout/stderr/trace 更新到 `failing_case`。

83. 如果当前用例仍失败，rtest 计算新的失败签名。失败签名基于归一化后的失败输出，用于判断连续多轮是不是卡在同一种失败上。

84. 如果失败签名与上一轮相同，rtest 增加 `stall_count`。当连续相同达到 `STALL_SAME_SIGNATURE_ROUNDS`，它会在历史里提示模型必须改变策略，例如请求 C 对照、查看另一个 Rust 文件、使用 probe，而不是继续改同一区域。

85. 如果失败签名改变，rtest 重置或降低停滞计数，并把新失败摘要写入历史。下一轮会基于新的失败现象继续修复。

86. 如果当前用例通过，rtest 不立刻接受修改。它调用 `_check_regression()` 重跑当前 summary 中已经通过的 baseline 用例，确认当前改动没有破坏已有行为。这里的 baseline 是本次套件状态里原本通过的脚本集合，不是历史上所有曾经通过过的脚本集合。

87. `_check_regression()` 只遍历 `runner.test_dir.glob("*.sh")` 找到的顶层脚本；脚本名在 baseline 集合中且不是当前修复脚本时，才调用 `runner.run_single(script, capture_trace=True)`。这里捕获 trace 是为了一旦出现回归，下一轮 prompt 能看到回归失败的具体 shell 路径。

88. 如果没有任何 baseline 用例回归，rtest 打印当前用例修复成功，并把该用例标记为已修复。当前 snapshot 不需要回滚，修改保留在 Rust 工程中。

89. 如果发现回归，rtest 格式化回归详情，例如哪些脚本原本通过、现在失败、exit code、stdout/stderr 尾部和 trace 尾部。

90. 回归发生后，rtest 调用 `ProjectSnapshot.restore()` 回滚当前用例修复期间对 Rust 工程的 edits，避免为了一个脚本破坏其他脚本。

91. 回滚后 rtest 重新执行 `cargo build --release`，并调用 `runner.restage_rust_binary()` 刷新 wrapper。这样后续测试使用的是回滚后的 Rust binary。

92. rtest 回滚后再次运行当前用例和 baseline 用例，检查回滚是否恢复了已通过行为。如果回滚后仍有异常，它会把这些结果也写入回归警告。

93. rtest 把回归警告写入 `state.regression_warning`，下一轮 prompt 会明确要求模型保持当前用例通过，同时解释为什么不会再次破坏这些回归用例。

94. 如果 LLM 返回 `complete=true` 但当前用例仍未通过，rtest 不接受完成标记。它会把“complete=true 但用例仍失败”写入历史，并要求下一轮提供新证据或新编辑。

95. 如果 LLM 既没有请求材料，也没有提交编辑，也没有请求有效 probe，rtest 会把空转写入历史，要求下一轮改变策略。空转仍消耗一次修复轮数。

96. 当前失败用例修复成功后，控制权回到 `SuiteRepairCoordinator`。套件协调器会重新查看测试汇总，选择下一个仍失败的脚本继续调用 `_repair_failure()`。

97. 单个失败用例返回后，协调器会重新定位 release binary、刷新 Rust wrapper，并调用 `runner.run_all(scripts)` 再跑全套脚本。这个全套复测依旧默认不捕获 trace，只有新失败进入修复循环时才懒加载 trace。

98. 如果全套测试都通过，rtest 打印“最终测试结果”，返回总数、通过数、失败数和每个脚本结果。

99. 如果仍有失败，但达到最大套件修复轮数或单用例最大修复轮数，rtest 打印未修复的用例名。若当前用例有未接受的 edits 且快照可用，它会尝试回滚本用例 edits。

100. 在 `try/finally` 的 finally 分支中，rtest 调用 `runner.cleanup()`。如果未启用 LogAgent，它会删除 `test/.bin` wrapper 目录、每个 `.run_<script>` 临时目录以及本次 run root；如果启用了 LogAgent，运行目录通常会保留，以便查看 runtime/probe 日志。

101. 最终主流程拿到 `TestRunSummary`。如果 `which` 的所有 shell 脚本通过，说明 Rust CLI 在这些测试覆盖的场景中已经与 C reference 行为对齐；如果仍有失败，summary 会保留失败脚本、exit code、stdout/stderr 尾部和可能的 trace，用于后续人工分析或继续增加修复轮数。
