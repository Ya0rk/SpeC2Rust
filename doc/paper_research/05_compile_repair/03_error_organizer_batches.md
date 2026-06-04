# ErrorOrganizerAgent 错误分批调研

## 研究问题

大规模翻译后的 Rust 工程常常一次产生几十到上百条 rustc 诊断。直接把完整 stderr 放进 prompt 会造成错误密度过高、主因和派生错误混杂、上下文窗口浪费。`ErrorOrganizerAgent` 的研究问题是：如何把长错误输出切分、聚类、分批，并为每批附加最小源码窗口，让修复 agent 一次只处理一个可操作的错误前沿。

## 流程 / 数据流

`ErrorOrganizerAgent.organize_errors(error_message, project_path)` 的数据流如下：

1. `_normalize_error_message()` 去掉 ANSI 转义，统一换行，压缩连续空行。
2. `_split_diagnostics()` 根据 `error[E...]`、`warning:`、`note:`、`help:`、`error: could not compile` 等头部切分诊断块。
3. `_extract_candidate_locations()` 从 `--> path:line:col` 提取候选文件和行号，只保留项目内真实存在的文件；涉及 `Cargo.toml` 时特殊补上该文件。
4. `_extract_error_code()` 把 `error[E0425]` 提取为 `E0425`，否则退化为 `warning`、`error` 或 `other`。
5. `_group_diagnostics()` 按 `error_code + primary_file` 聚类，并记录候选文件和候选行号。
6. `organize_errors()` 按 `batch_size` flush 批次。每批包含 `diagnostics`、`candidate_files`、`candidate_contexts`、`context_text` 和中文 `summary`。
7. `RustRepairAgent._select_repair_error_batch()` 只取第 1 批作为活跃批次，把批次说明和源码窗口写入诊断 prompt / 编辑 prompt。
8. 每次重新 `cargo check` 后重新组织错误。如果活跃批次签名变化，`RustRepairAgent` 会重新请求诊断计划，并在 journal 中记录 `organized_error_batch_switched`。

legacy `CodeFixer` 也能使用该 organizer：它遍历批次，把当前批的诊断和 `context_text` 交给候选文件选择逻辑，并限制候选文件数为前 10 个。

## 关键工程细节

- **错误密度控制。** 默认 `batch_size=10`，`scripts/agent.sh` 默认传入 `--error-batch-size 10`。当批内诊断超过上限时，overflow 会进入下一批。
- **按错误码和主文件聚类。** 同一错误码、同一主文件的诊断更可能共享修复原因，适合放在同一批。
- **上下文窗口自动合并。** 每个候选行号默认取前后 15 行；相邻窗口重叠时合并，减少重复源码片段。
- **真实行号格式。** `context_text` 使用 `0005 | ...` 形式保留真实行号，和 `RustRepairAgent` 编辑协议中的行号约束一致。
- **未知位置后置。** 分组排序把有主文件的诊断排在前面，`__unknown__` 后置，优先处理可定位错误。
- **批次解释注入 prompt。** active batch context 会说明当前是 `1/N` 批、总诊断数、活跃批摘要和源码上下文，提示 LLM 先修当前批。

## 可引用代码证据

| 证据点 | 代码位置 |
| --- | --- |
| 默认开启错误 organizer 和 batch size | `scripts/agent.sh:165` |
| main 中构造 `ErrorOrganizerAgent(batch_size=args.error_batch_size)` | `src/agent/main.py:451`、`src/agent/main.py:456` |
| ANSI 清理和换行规范化 | `src/agent/error_organizer_agent.py:18`、`src/agent/error_organizer_agent.py:22` |
| 诊断块切分 | `src/agent/error_organizer_agent.py:36` |
| 提取候选文件、行号和 `Cargo.toml` | `src/agent/error_organizer_agent.py:66` |
| 生成 +/-15 行窗口 | `src/agent/error_organizer_agent.py:87` |
| 错误码提取和分组键 | `src/agent/error_organizer_agent.py:118`、`src/agent/error_organizer_agent.py:132` |
| 批次结构、上下文和 summary | `src/agent/error_organizer_agent.py:175`、`src/agent/error_organizer_agent.py:239` |
| 批次大小控制和 overflow | `src/agent/error_organizer_agent.py:264`、`src/agent/error_organizer_agent.py:285` |
| `RustRepairAgent` 选择一个活跃批次 | `src/agent/rust_repair_agent.py:796` |
| 批次切换后重新诊断并写 journal | `src/agent/rust_repair_agent.py:3933`、`src/agent/rust_repair_agent.py:3966` |
| 单测：源码上下文窗口为 5-35 行 | `src/tests/test_error_organizer_agent.py:13` |
| 单测：诊断 prompt 包含 `2 remaining organized batches` | `src/tests/test_rust_repair_agent.py:88` |

## 实验钩子

- **batch size 消融：** 对比 `--error-batch-size 1/5/10/20` 的最终通过率、平均 LLM 调用次数、平均 prompt 字符数和轮内停滞率。
- **有无 organizer：** 关闭 `--use-error-organizer-agent`，观察错误多的项目是否更容易在首轮 prompt 中偏离主因。
- **批次切换统计：** 统计 journal 中 `organized_error_batch_switched` 的次数，衡量修复一个批次后暴露下一批的频率。
- **上下文窗口贡献：** 删除 `candidate_contexts` 或缩小 radius，比较需要额外 `more_read_requests` 的次数。
- **错误定位覆盖率：** 对每个项目统计有 `candidate_files` 的诊断比例、`__unknown__` 比例和 `Cargo.toml` 批次数。

## 局限与反例

- `note:` 和 `help:` 也被当作诊断头，可能和父错误切开，导致一个错误的解释信息进入其他批次。
- `--> ([^:\n]+):line:col` 对 Windows 绝对路径的盘符冒号不友好；如果 rustc 输出绝对路径，候选文件提取可能失败。
- 只保留真实存在的候选文件，会漏掉 `mod foo;` 指向但尚未创建的缺失文件场景。
- 按错误码和主文件排序不理解依赖关系；第 1 批未必是真正根因。例如 `Cargo.toml` 依赖缺失可能导致许多文件的 unresolved import。
- 默认 15 行窗口对跨文件接口、trait impl 和宏展开错误可能不足，仍需要 LLM 通过 `search_requests` 扩展证据。
- warnings 也可能进入批次，若目标只是编译通过，可能浪费修复预算。

## 可写入论文位置

- **方法章节：** 「错误分批与活跃错误前沿」小节，说明如何降低 prompt 噪声。
- **系统实现章节：** 写诊断规范化、错误码 + 文件聚类、源码窗口和 active batch prompt。
- **实验章节：** 作为 organizer 消融，报告不同 batch size 下的修复效率和成功率。
