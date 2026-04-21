# C 文档去重与 Rust 生成约束优化方案

更新时间：2026-04-20

## 1. 背景

当前项目的迁移链路是：

1. `SpecAgent` 读取 C 项目，生成迁移文档。
2. `RustAgent` 读取这些文档和解析 JSON，再生成 Rust 工程。

这个方向是合理的：先把 C 代码压缩成可审查的事实文档，再让 Rust 生成阶段追求更地道的 Rust 写法。但现在样例 `output/avl-tree/c_docs` 暴露出两个直接问题：

- 文档之间重复搬运同一批函数、接口、行为和测试要求，导致上下文膨胀。
- Rust 端受到“Rust 最佳实践”“完整工程”“推荐依赖”等提示影响，会生成超出原 C 项目的高级功能、文件和发布流程。

本方案只讨论优化设计，不涉及代码修改。代码修改待你审查确认后再做。

## 2. 已阅读范围

### 2.1 代码路径

- `src/agent/spec_agent.py`
- `src/agent/rust_agent.py`
- `src/config/prompt.py`
- `src/config/config.py`

### 2.2 样例文档路径

- `output/avl-tree/c_docs/docs/rewrite-context/00_repo_manifest.md`
- `output/avl-tree/c_docs/docs/rewrite-context/01_subsystems/main_root.md`
- `output/avl-tree/c_docs/docs/rewrite-context/02_interfaces/001_public_interfaces.md`
- `output/avl-tree/c_docs/docs/rewrite-context/02_interfaces/002_main_root.md`
- `output/avl-tree/c_docs/docs/rewrite-context/03_behaviors/001_behavior_specification.md`
- `output/avl-tree/c_docs/.specify/memory/constitution.md`
- `output/avl-tree/c_docs/specs/001-main_root-rust-port/spec.md`
- `output/avl-tree/c_docs/specs/001-main_root-rust-port/plan.md`
- `output/avl-tree/c_docs/specs/001-main_root-rust-port/tasks.md`

### 2.3 对照事实

- `datasets/avl-tree/avl_bf.h`
- `datasets/avl-tree/avl_data.h`
- `datasets/avl-tree/README.md`
- `src/parse/res/avl-tree.json`

## 3. 当前链路梳理

### 3.1 `SpecAgent` 的文档生成流程

`SpecAgent.analyze_and_generate_spec()` 的主流程可以分成 3 层：

1. 静态分析层：
   - `_collect_project_info()`
   - `self.parser.analyze_directory(...)`
   - `_build_dependency_graph(...)`
   - `_split_modules(...)`

2. 认知压缩层：
   - `_generate_repo_manifest(...)`
   - `_generate_subsystem_docs(...)`
   - `_generate_interfaces_docs(...)`
   - `_generate_behaviors_docs(...)`
   - `_generate_constitution(...)`

3. 执行规划层：
   - `_generate_spec_per_module(...)`
   - `_generate_plan_per_module(...)`
   - `_generate_tasks_per_module(...)`
   - `_write_module_auxiliary_notes(...)`

其中，`00_repo_manifest.md`、`01_subsystems/*.md`、`02_interfaces/*.md` 已经大量改成了事实型模板拼装；而 `03_behaviors`、`constitution.md`、`spec.md`、`plan.md`、`tasks.md` 仍主要依赖 LLM 生成。

### 3.2 `RustAgent` 的文档读取与生成流程

`RustAgent.generate_from_docs()` 的主流程是：

1. `create_rust_project(...)` 创建或复用 Rust 项目。
2. `load_documents(doc_paths)` 递归读取传入路径下所有 `.md` 文件。
3. `configure_source_context(...)` 加载 `src/parse/res/*.json`，构造源码摘要和接口约束。
4. `_generate_project_structure()` 把所有文档拼成 `all_docs`，让模型设计 Rust 项目结构。
5. `_parse_file_list()` 从模型输出解析文件列表。
6. `_sanitize_generation_file_list()` 过滤非法路径、测试、示例和 benchmark。
7. `_generate_implementation_plan()` 根据项目结构和文件列表生成实现计划。
8. `generate_code()` 按文件逐个调用 `_generate_single_file()`。

这个设计的关键风险是：Rust 工程文件集合先由模型规划，再由本地代码做有限过滤。当前过滤能去掉 `tests/`、`examples/`、`benches/`、`.github/` 等路径，但所有 `src/*.rs` 都会被视为可生成文件。因此只要模型规划了 `src/safety.rs`、`src/sync.rs`、`src/recovery.rs`、`src/ffi.rs`，它们就可能进入后续生成链路。

## 4. 样例问题诊断

### 4.1 文档重复的具体表现

`avl-tree` 样例只有 4 个 C 文件和 3 个头文件，但 `c_docs` 下 9 个文档合计约 66 KB。重复主要来自以下几类：

1. 函数事实重复：
   - `01_subsystems/main_root.md` 列出 `avl_create`、`avl_destroy`、`avl_find` 等核心接口。
   - `02_interfaces/002_main_root.md` 再完整列出同一批函数。
   - `03_behaviors/001_behavior_specification.md` 再用自然语言复述这些函数的操作流程。
   - `spec.md` 再定义一遍 Rust 侧函数需求。
   - `plan.md` 和 `tasks.md` 再按阶段重复拆分这些函数。

2. 测试和质量要求重复：
   - `spec.md` 有测试场景和成功标准。
   - `plan.md` 有测试阶段和成功标准。
   - `tasks.md` 有测试任务、里程碑、验收标准。
   - `constitution.md` 又包含质量关卡和性能基准要求。

3. 工程结构重复：
   - `plan.md` 给出 `src/node.rs`、`src/tree.rs`、`src/iter.rs` 等模块映射。
   - `tasks.md` 后半段又给出更大的模块文件结构。
   - Rust 端还会重新让模型生成项目结构，形成第三份工程结构来源。

### 4.2 冗余和越界内容

样例里最明显的越界内容包括：

- `plan.md` 推荐 `anyhow`、`thiserror`、`criterion`、`pretty_assertions`，但原 C 项目没有证据要求这些依赖。
- `plan.md` 引入范围查询、批量操作、`serde` 序列化、高级 API。
- `tasks.md` 规划属性测试、性能测试、线程安全、恢复机制、FFI、发布到 `crates.io`、`LICENSE`、`CHANGELOG`、`PERFORMANCE.md`。
- `tasks.md` 的文件树包含 `src/safety.rs`、`src/sync.rs`、`src/validation.rs`、`src/recovery.rs`、`src/ffi.rs`。

这些不是“文档写得详细”，而是把 C 项目迁移任务扩大成了一个全新 Rust crate 的产品路线图。

### 4.3 事实不准确和推断过度

样例中有几处事实偏差会直接误导 Rust 生成：

1. 结构体抽取不稳定：
   - `02_interfaces/002_main_root.md` 中 6 个结构体都显示为 `anonymous`。
   - 同时 `tasks.md` 又写“数据结构数量：0 个”。
   - 实际 `avl_bf.h` 里有 `avlnode` 和 `avltree`，`avl_data.h` 里有 `mydata`。

2. 数据模型被模型重写：
   - 原始 `avlnode` 存储的是 `char bf`，表示 balance factor。
   - `plan.md` 中假设 C 节点有 `height` 和 `balance_factor` 字段，还建议 Rust 节点保存 `height: i32` 和 `balance_factor: i32`。
   - 这与 README 中“store balance factor, not height”的选择冲突。

3. 行为文档含有推断语气：
   - `03_behaviors` 中出现“可能”“推断”“需查看实现”“未知”等表达。
   - 这类内容一旦进入 Rust 端，会被模型当作设计空间，而不是待确认缺口。

4. 函数角色混杂：
   - `02_interfaces/002_main_root.md` 把 `avl_bf.c` 的库函数、`avl_example.c` 的 `main`、`avl_test.c` 的测试函数、测试辅助函数全部放进“函数”章节。
   - 这会让 Rust 端误以为 49 个函数都是产品实现范围。

## 5. 根因分析

### 5.1 文档缺少“唯一事实源”

当前每份文档都倾向于自洽完整，因此都会复述函数、数据结构、行为、测试和成功标准。这样对人类阅读友好，但对下游 agent 是噪声。

建议改成：

- 接口事实只在 `interfaces` 或机器可读 contract 中完整出现一次。
- 其他文档只引用接口 ID，不重复签名表。
- 行为文档只描述行为事实，不重复 API 清单。
- 任务文档只描述要做什么，不重新设计产品路线。

### 5.2 模型生成文档承担了太多设计职责

`generate_module_spec`、`generate_module_plan`、`generate_module_tasks` 的 prompt 都要求“完整 spec-kit 文档”“Rust 最佳实践”“推荐 crates”“完整任务阶段”。这会自然诱导模型补全一个理想 Rust 工程，而不是压缩原 C 工程的迁移范围。

### 5.3 Rust 端的生成边界太软

`RustAgent` 的本地限制主要是文件类型过滤，而不是迁移范围过滤。它能判断“这个路径是不是 Rust 工程文件”，但不能判断“这个文件是否被原 C 项目证据允许”。

### 5.4 文档读取没有优先级和预算

`load_documents()` 递归读取所有 Markdown。`_generate_project_structure()` 再把所有内容拼起来，导致模型同时看到：

- 仓库事实
- 接口事实
- 行为推断
- 宪法原则
- spec
- plan
- tasks

这些文档之间如果有矛盾或重复，模型不会知道哪个优先级最高。

## 6. 优化目标

### 6.1 文档目标

1. 减少重复：同一类事实只保留一个 canonical 来源。
2. 减少冗余：删除没有 C 证据、没有配置启用、没有迁移必要的内容。
3. 增强可追溯：每条重要需求都能回到 C 文件、行号、函数 ID 或宏 ID。
4. 区分事实和缺口：不能把“未知”写成“也许应该实现”。

### 6.2 Rust 生成目标

1. Rust 语法和结构可以地道，但功能范围必须等价于原 C 项目。
2. 生成文件集合必须受 contract 约束，不能由模型无限扩展。
3. 依赖必须默认最小化，除非 contract 或配置允许。
4. 测试、示例、benchmark、FFI、发布工程必须由配置显式开启。

## 7. 新文档架构建议

建议把文档分为“人类可读文档”和“机器可读 contract”两层。

### 7.1 机器可读层：`translation_contract.json`

新增一个核心文件：

```text
docs/rewrite-context/translation_contract.json
```

它应该成为 RustAgent 的第一优先级输入。示例结构：

```json
{
  "project": {
    "name": "avl-tree",
    "kind": "library",
    "source_files": ["avl_bf.c", "avl_data.c"],
    "example_files": ["avl_example.c"],
    "test_files": ["avl_test.c"],
    "header_files": ["avl_bf.h", "avl_data.h", "minunit.h"]
  },
  "generation_boundary": {
    "allowed_rust_files": [
      "Cargo.toml",
      "src/lib.rs",
      "src/avl.rs",
      "src/data.rs",
      "README.md"
    ],
    "allow_tests": false,
    "allow_examples": false,
    "allow_benches": false,
    "allow_ffi": false,
    "dependency_policy": "std_only_by_default"
  },
  "forbidden_without_evidence": [
    "serde",
    "criterion",
    "proptest",
    "thread_safe_api",
    "recovery_mechanism",
    "crates_io_release",
    "range_query",
    "batch_operation"
  ],
  "types": [
    {
      "id": "TYPE-avlnode",
      "name": "avlnode",
      "source": "avl_bf.h:30-36",
      "fields": [
        {"name": "left", "c_type": "struct avlnode *"},
        {"name": "right", "c_type": "struct avlnode *"},
        {"name": "parent", "c_type": "struct avlnode *"},
        {"name": "bf", "c_type": "char"},
        {"name": "data", "c_type": "void *"}
      ]
    }
  ],
  "functions": [
    {
      "id": "FN-avl_create",
      "name": "avl_create",
      "role": "public_api",
      "source": "avl_bf.c:28-54",
      "declared_in": "avl_bf.h",
      "signature": "avltree *avl_create(int (*compare_func)(const void *, const void *), void (*destroy_func)(void *));"
    }
  ]
}
```

重点不是 JSON 字段一次设计完美，而是让 Rust 端有一个稳定、结构化、可校验的范围边界。

### 7.2 人类可读层：精简 Markdown

建议保留以下 Markdown：

```text
docs/rewrite-context/
  00_repo_manifest.md
  01_interfaces.md
  02_behavior_notes.md
  03_migration_scope.md
specs/<module>/
  spec.md
  plan.md
  tasks.md
```

但各文档职责要收窄：

1. `00_repo_manifest.md`
   - 只保留文件清单、构建入口、README 的“项目自身选择”摘要。
   - 不再整段摘录通用算法介绍，避免模型被 README 中的理论内容带偏。

2. `01_interfaces.md`
   - 只保留接口事实索引。
   - 函数、宏、类型都有 ID。
   - 用角色区分：`public_api`、`internal_helper`、`example_entry`、`test_case`、`test_helper`。

3. `02_behavior_notes.md`
   - 只记录能从源码片段或解析 JSON 支撑的行为。
   - 每条行为引用函数 ID。
   - 证据不足时只写“缺口”，不写“可能”。

4. `03_migration_scope.md`
   - 明确本轮 Rust 迁移的边界。
   - 明确允许和禁止生成的文件、功能、依赖。
   - 这是 RustAgent 的人类可读范围说明。

5. `spec.md`
   - 只保留迁移目标、功能边界、验收标准。
   - 不重复完整函数签名。

6. `plan.md`
   - 只保留文件映射和实现顺序。
   - 不推荐无证据依赖。
   - 不包含发布、性能报告、线程安全、序列化等产品化内容。

7. `tasks.md`
   - 只保留可执行任务。
   - 任务必须来自 `plan.md` 的文件映射和 `translation_contract.json` 的函数清单。
   - 不允许后续续写出 Phase 8、Phase 9、Phase 10 这类越界阶段。

## 8. `SpecAgent` 优化建议

### 8.1 增加 contract 生成

在 `_generate_interfaces_docs()` 或其后新增：

```text
_generate_translation_contract(project_info, project_analysis, module_units, output_dir)
```

它负责生成：

- 项目分类：library、cli、mixed、test-only 等。
- 源文件角色：production、header、example、test、support。
- 函数角色：public API、internal helper、example entry、test case、test helper。
- 类型事实：结构体名、字段、来源位置。
- 宏事实：宏名、值、用途分类。
- Rust 生成边界：允许文件、默认依赖策略、禁止功能列表。

### 8.2 修复函数角色分类

建议规则：

1. 在 `.h` 中有声明的函数：`public_api`。
2. 只在 `.c` 中定义且被 public API 调用的函数：`internal_helper`。
3. `*_example.c` 中的 `main`：`example_entry`。
4. `*_test.c` 中的 `main`、`unit_test_*`、`all_tests`：`test_case` 或 `test_runner`。
5. `*_test.c` 中的 `tree_*`、`permute`、`swap`：`test_helper`。

这样 `RustAgent` 默认只实现 production 范围；如果 `generate_tests=true`，再把 test role 加入计划。

### 8.3 修复结构体事实抽取

当前样例中 `avlnode` 被拆成多个 `anonymous`，需要在 `SpecAgent` 侧做去重和命名恢复：

1. 优先读取 `typedef struct <name> { ... } <alias>;` 的 alias。
2. 对 `typedef struct { ... } avltree;` 这种匿名 struct，用 typedef alias 作为名称。
3. 按 `(file, start_line, end_line, normalized_declaration)` 去重。
4. 字段级解析失败时，至少保留完整 struct 声明片段，不要把字段误当成多个匿名 struct。

对 `avl-tree`，期望得到：

- `avlnode`
- `avltree`
- `mydata`

而不是 6 个 `anonymous`。

### 8.4 将 `spec.md`、`plan.md`、`tasks.md` 改成窄模板

短期可以继续用 LLM，但 prompt 必须变成 contract-first：

- 每条需求必须引用 contract ID。
- 不得新增 contract 中不存在的功能。
- 不得推荐 contract 未允许的依赖。
- 不得生成 tests/examples/benches，除非配置开启。
- 不得生成发布、crate 发布、CHANGELOG、许可证、性能报告等任务。

中期建议：

- `tasks.md` 改成确定性模板生成，LLM 只补充少量解释。
- `plan.md` 的文件映射由 contract 确定，LLM 不能新增文件。
- `spec.md` 只做范围说明和验收标准。

### 8.5 控制 README 摘录

`00_repo_manifest.md` 当前直接摘录 README，容易把通用算法介绍喂给 Rust 端。建议改为：

- 只提取 README 中的项目选择：
  - store balance factor
  - store parent reference
  - non-recursive iterative
  - file role list
- 删除 AVL 理论介绍、算法背景、论文历史等通用内容。

### 8.6 文档生成后增加 lint

新增一个文档 lint 阶段，检查生成内容是否越界：

```text
_lint_generated_docs(output_dir, contract)
```

建议规则：

- 禁止词：`crates.io`、`CHANGELOG`、`LICENSE`、`发布`、`恢复机制`、`线程安全`、`Send`、`Sync`、`serde`、`proptest`、`criterion`、`FFI`。
- 允许例外：contract 或 config 明确启用。
- 禁止推断词进入事实文档：`可能`、`推断`、`大概`、`也许`。
- 对 `spec.md`、`plan.md`、`tasks.md` 做最大长度限制。
- 检查 `tasks.md` 中的文件路径是否都在 contract 的 `allowed_rust_files` 中。

## 9. `RustAgent` 优化建议

### 9.1 优先读取 contract

`load_documents()` 可以保留，但 `RustAgent` 应先尝试读取：

```text
docs/rewrite-context/translation_contract.json
```

新增状态：

```text
self.translation_contract
self.allowed_rust_files
self.forbidden_features
self.dependency_policy
```

如果 contract 存在，项目结构和文件列表必须以 contract 为上限。

### 9.2 文件列表从“模型规划”改成“模型建议 + 本地裁决”

当前逻辑是：

1. 模型生成项目结构。
2. 本地解析文件。
3. 本地过滤非法文件类型。

建议改为：

1. contract 生成默认文件列表。
2. 模型只能在默认文件列表内解释职责，不能新增文件。
3. 如果模型输出额外文件，本地直接丢弃并记录 warning。

也就是把 `_sanitize_generation_file_list()` 从“文件类型过滤器”升级为“范围裁决器”。

### 9.3 限制 `src/*.rs` 的自由扩展

现在 `_is_supported_generation_file()` 允许所有 `src/*.rs`。这太宽。建议增加：

```text
if self.allowed_rust_files:
    return normalized in self.allowed_rust_files
```

无 contract 时再走现有逻辑作为兼容回退。

### 9.4 限制依赖

当前 `prompt.py` 会让 `plan.md` 推荐依赖，`RustAgent._detect_dependencies()` 还会根据生成代码补依赖。建议：

1. 默认依赖策略为 `std_only_by_default`。
2. `Cargo.toml` 只能包含 contract 允许的依赖。
3. 任何新增依赖都必须有 `reason` 和 `evidence_id`。
4. 如果模型生成 `serde`、`thiserror`、`anyhow` 等未授权依赖，本地清理或拒写。

### 9.5 生成 prompt 增加硬边界

`generate_project_structure_prompt`、`generate_implementation_plan_prompt`、`generate_code_prompt` 都需要加入类似约束：

```text
你只能实现 migration contract 中列出的功能。
不得新增原 C 项目没有证据支持的功能、模块、依赖、测试框架、发布流程或兼容层。
如果某个 Rust 惯用抽象会扩大功能范围，禁止生成。
地道 Rust 只体现在所有权、模块边界、错误表达和命名上，不代表新增 API。
```

对于 `avl-tree`，要明确：

- 不生成范围查询。
- 不生成批量构建。
- 不生成 `serde`。
- 不生成线程安全封装。
- 不生成恢复机制。
- 不生成 `crates.io` 发布任务。
- 不生成 FFI，除非后续显式开启。

### 9.6 文档上下文按优先级拼接

建议 Rust 端上下文优先级：

1. `translation_contract.json`
2. 原始 C 源码相关片段
3. `03_migration_scope.md`
4. `01_interfaces.md`
5. `02_behavior_notes.md`
6. `spec.md`、`plan.md`、`tasks.md` 的精简摘要

低优先级文档不应该覆盖高优先级事实。可以在上下文中明确写：

```text
优先级规则：contract > C source snippets > migration_scope > interfaces > behavior_notes > spec/plan/tasks。
当文档冲突时，忽略低优先级内容。
```

## 10. Prompt 方向调整

### 10.1 `generate_module_spec`

当前问题：

- 要求“完整功能规格”，容易让模型扩写场景。
- 没有要求每条需求引用源码证据。

建议改成：

- 只列 contract 中 `public_api` 的迁移目标。
- 用户场景只来自 example/test 源码，不自由发挥。
- 成功标准只包含可验证行为等价、编译、核心测试。

### 10.2 `generate_module_plan`

当前问题：

- 要求推荐依赖和 Rust 最佳实践。
- 诱导生成泛型、迭代器、序列化、高级 API。

建议改成：

- 文件映射必须来自 contract。
- 依赖默认空。
- 数据模型必须从 C struct 字段出发。
- 不允许写“C 结构体（假设）”。
- 不允许把 balance factor 模型改成 height 模型，除非源码证据支持。

### 10.3 `generate_module_tasks`

当前问题：

- 模型会在续写中追加 Phase 8、Phase 9、Phase 10。
- 任务变成产品发布路线图。

建议改成：

- 任务数量和范围由 contract 决定。
- 只包含 `setup`、`types`、`core functions`、`verification`。
- 每个任务必须绑定 `function_id`、`type_id` 或 `file_id`。
- 不允许 optional phase。

### 10.4 `generate_project_structure`

当前问题：

- 示例项目结构包含 `tests/`。
- 要求“核心数据结构和 trait 设计”“错误处理策略”，容易引导额外抽象。

建议改成：

- 如果 contract 存在，直接给定允许文件树，让模型只补充每个文件职责。
- 删除示例中的 `tests/`，除非 `generate_tests=true`。
- 删除 “trait 设计” 作为必填项。

## 11. 针对 `avl-tree` 的期望输出边界

如果按当前默认配置：

```text
generate_tests=false
generate_examples=false
generate_benches=false
allow_ffi=false
dependency_policy=std_only_by_default
```

那么 `avl-tree` 的 Rust 文件集合建议控制在：

```text
Cargo.toml
src/lib.rs
src/avl.rs
src/data.rs
README.md
```

如果希望更细分，也应有明确上限：

```text
Cargo.toml
src/lib.rs
src/types.rs
src/tree.rs
src/balance.rs
src/data.rs
README.md
```

不应默认生成：

```text
src/iter.rs
src/error.rs
src/safety.rs
src/sync.rs
src/validation.rs
src/recovery.rs
src/ffi.rs
tests/*.rs
examples/*.rs
benches/*.rs
CHANGELOG.md
PERFORMANCE.md
include/*.h
```

除非 contract 或配置明确开启。

## 12. 分阶段实施计划

### 阶段 1：低风险快速收敛

目标：先止住重复和越界，不重构大架构。

修改点：

1. 收紧 `src/config/prompt.py`：
   - 删除推荐依赖、发布、可选高级功能暗示。
   - 明确禁止无证据功能。
   - 明确 `tasks.md` 不得追加 optional phase。

2. 收紧 `RustAgent._sanitize_generation_file_list()`：
   - 增加禁止文件名黑名单：`safety.rs`、`sync.rs`、`recovery.rs`、`ffi.rs` 等。
   - 黑名单只作为临时方案，后续用 contract 替换。

3. 限制 README 摘录：
   - `SpecAgent._build_repo_manifest_content()` 不再直接放长 README。
   - 只保留项目选择和文件角色。

预期收益：

- `tasks.md` 不再膨胀出 Phase 8-10。
- Rust 端不再轻易生成恢复、线程安全、发布相关文件。
- 上下文体积立即下降。

### 阶段 2：引入 `translation_contract.json`

目标：建立唯一事实源。

修改点：

1. 在 `SpecAgent` 增加 contract builder。
2. 在 `RustAgent` 增加 contract loader。
3. `RustAgent` 的文件列表生成受 `allowed_rust_files` 约束。
4. Prompt 中明确 contract 优先级最高。

预期收益：

- 文件集合不再由模型自由决定。
- 文档即使有冗余，Rust 端也有硬边界。
- 后续可以对 contract 做单元测试。

### 阶段 3：重构 Markdown 文档职责

目标：真正去重。

修改点：

1. 合并 `01_subsystems` 和 `02_interfaces` 中重复的函数列表。
2. 行为文档改成引用函数 ID，不复述签名。
3. `spec.md`、`plan.md`、`tasks.md` 改成 contract-driven 窄文档。
4. `constitution.md` 改成短模板，或降级为非 RustAgent 默认输入。

预期收益：

- `c_docs` 总体积明显下降。
- 每份文档的职责更清晰。
- Rust 生成上下文更稳定。

### 阶段 4：增加文档和生成校验

目标：防止回归。

新增校验：

1. 文档 lint：
   - 禁止越界词。
   - 禁止推断词进入事实文档。
   - 检查任务文件路径是否在 allowed list 中。

2. Rust 生成前校验：
   - 模型项目结构不得包含 contract 外文件。
   - 依赖不得超出 allowlist。
   - `src/*.rs` 数量不得超过 contract 文件数上限。

3. 样例回归：
   - `avl-tree` 不出现 `src/sync.rs`、`src/recovery.rs`、`src/ffi.rs`。
   - `tasks.md` 不出现 Phase 8-10。
   - `plan.md` 不出现 `serde`、`criterion`、`crates.io`。

## 13. 验收标准

### 13.1 文档侧

以 `avl-tree` 为例：

- `c_docs` 总体积减少至少 40%。
- 函数签名只在 canonical interface/contract 中完整出现。
- `spec.md`、`plan.md`、`tasks.md` 不再重复完整函数清单。
- 不出现未授权的 `serde`、`criterion`、`proptest`、`FFI`、线程安全、恢复机制、发布任务。
- 不出现“C 结构体（假设）”。
- `avlnode`、`avltree`、`mydata` 能被正确识别。

### 13.2 Rust 生成侧

以默认配置为例：

- 生成文件只来自 allowed list。
- `Cargo.toml` 不添加无证据依赖。
- 不生成 tests/examples/benches。
- 不生成发布相关文件。
- Rust 代码实现范围对应 C public API 和必要 internal helper。

### 13.3 行为侧

- `avl_create` 保留 root/nil sentinel 初始化语义。
- `avl_insert` 保留 `AVL_DUP`、`AVL_MIN` 相关行为。
- `avl_delete` 保留 `keep` 参数语义：`keep == 0` 时调用 destroy 并返回 `NULL`，否则返回 data。
- `avl_apply` 保留 preorder/inorder/postorder 以及回调非零提前返回语义。
- 平衡逻辑以 `bf` 为核心，不默认改成存储 absolute height。

## 14. 风险和取舍

### 14.1 过度收缩的风险

如果收得太紧，可能会让 Rust 版本不够“地道”。解决方式是把“地道”限定在表达方式上，而不是功能范围上：

- 可以用 Rust 所有权表达节点生命周期。
- 可以用模块拆分组织代码。
- 可以用 `Option`/`Result` 表达错误。
- 但不能新增没有 C 证据的 API 和产品功能。

### 14.2 contract 设计成本

引入 `translation_contract.json` 会增加前期实现成本，但收益很大：

- 可测试。
- 可 lint。
- 可给不同 agent 共用。
- 能防止模型把文档当自由创作空间。

### 14.3 spec-kit 兼容

如果后续仍要保留 spec-kit 的 `spec.md`、`plan.md`、`tasks.md`，可以继续生成这些文件，但它们应成为 contract 的视图，而不是新的事实源。

## 15. 推荐优先级

推荐先做以下 5 件事：

1. 收紧 `prompt.py` 中 `generate_module_plan` 和 `generate_module_tasks`，禁止无证据扩展。
2. 给 `RustAgent` 增加 allowed file list 机制，至少先支持从文档或配置中读取。
3. 新增 `translation_contract.json`，把函数角色、类型事实和生成边界结构化。
4. 修复 struct 去重和命名恢复，避免 `anonymous` 污染下游。
5. 增加文档 lint，防止 Phase 8-10、发布、线程安全、恢复机制再次出现。

这 5 件事完成后，再考虑进一步压缩 Markdown 体积和调整 spec-kit 文档结构。

