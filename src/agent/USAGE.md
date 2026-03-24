# C 到 Rust 项目转换 Agent 使用说明

## 概述

这个工具可以自动将 C 项目转换为 Rust 实现，完整流程包括：

1. **分析 C 项目** - 使用多轮迭代方式分析 C 项目并生成详细文档
2. **生成 Rust 代码** - 基于 C 项目文档生成地道的 Rust 实现
3. **编译修复** - 自动修复 Rust 代码的编译错误
4. **测试修复** - 自动修复 Rust 代码的测试失败

## 快速开始

### 基本用法

```bash
python agent/main.py /path/to/c/project /path/to/output
```

这会：
- 分析 `/path/to/c/project` 目录下的 C 项目
- 在 `/path/to/output` 目录生成文档和 Rust 项目
- Rust 项目默认名为 `rust_implementation`

### 完整示例

```bash
# 分析 avl-tree 项目并转换为 Rust
python agent/main.py datasets/avl-tree output/avl-tree-rust

# 指定 Rust 项目名称
python agent/main.py datasets/avl-tree output/avl-tree-rust --rust-project-name avl_tree_rs

# 使用更大的模型（更准确但更慢）
python agent/main.py datasets/avl-tree output/avl-tree-rust --model-size 32

# 增加修复迭代次数（处理复杂项目）
python agent/main.py datasets/avl-tree output/avl-tree-rust --max-fix-iterations 10
```

## 命令行参数

### 必需参数

| 参数 | 说明 |
|------|------|
| `c_project_path` | C 项目路径 |
| `output_dir` | 输出目录（文档和 Rust 项目保存位置） |

### 可选参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--rust-project-name` | `rust_implementation` | Rust 项目名称 |
| `--model-size` | `14` | 模型大小（7/14/32/72） |
| `--max-fix-iterations` | `5` | 最大修复迭代次数 |
| `--skip-c-analysis` | `False` | 跳过 C 项目分析步骤 |
| `--skip-code-fix` | `False` | 跳过代码修复步骤 |
| `--skip-test-fix` | `False` | 跳过测试修复步骤 |

## 使用场景

### 场景 1：完整转换（默认）

```bash
python agent/main.py datasets/avl-tree output/avl-tree-rust
```

执行所有步骤：
1. ✓ 分析 C 项目
2. ✓ 生成 Rust 代码
3. ✓ 编译修复
4. ✓ 测试修复

### 场景 2：已有 C 项目文档

如果已经有 C 项目的分析文档：

```bash
python agent/main.py datasets/avl-tree output/avl-tree-rust --skip-c-analysis
```

将文档放在 `output/avl-tree-rust/c_docs/` 目录下。

### 场景 3：只生成代码，不修复

```bash
python agent/main.py datasets/avl-tree output/avl-tree-rust --skip-code-fix --skip-test-fix
```

适用于快速查看生成的代码，不关心编译和测试。

### 场景 4：处理复杂项目

对于复杂项目，可能需要更多迭代次数：

```bash
python agent/main.py datasets/complex-project output/complex-rust --max-fix-iterations 15
```

### 场景 5：使用更大模型

对于重要项目，使用更大的模型提高质量：

```bash
python agent/main.py datasets/avl-tree output/avl-tree-rust --model-size 72
```

## 输出结构

```
output/
├── c_docs/                     # C 项目文档
│   ├── doc_skeleton.md        # 文档骨架
│   ├── iteration_1.md         # 第 1 轮迭代分析
│   ├── iteration_2.md         # 第 2 轮迭代分析
│   ├── iteration_3.md         # 第 3 轮迭代分析
│   ├── final_project_overview.md  # 最终项目文档
│   └── analysis_history.json  # 分析历史
│
└── rust_implementation/        # Rust 项目
    ├── Cargo.toml             # Cargo 配置
    ├── src/
    │   └── lib.rs             # 主库文件
    ├── tests/                 # 测试文件
    └── ...
```

## 流程详解

### 步骤 1: C 项目分析

- 使用 `CDocAgent` 分析 C 项目
- 生成多轮迭代分析文档
- 输出到 `c_docs/` 目录
- 包含：
  - 项目架构分析
  - 函数和数据结构分析
  - 核心算法分析
  - 源代码位置引用

### 步骤 2: Rust 代码生成

- 使用 `RustAgent` 根据 C 项目文档生成 Rust 代码
- 创建新的 Rust 项目
- 生成符合 Rust 惯用法的代码
- 包含单元测试

### 步骤 3: 编译修复

- 使用 `CodeFixer` 自动修复编译错误
- 执行流程：
  1. `cargo fmt` - 格式化代码
  2. `cargo check` - 检查代码
  3. `cargo build` - 编译代码
- 多轮迭代直到编译通过或达到最大迭代次数

### 步骤 4: 测试修复

- 使用 `TestFixer` 自动修复测试失败
- 执行流程：
  1. `cargo test` - 运行测试
  2. 分析失败原因
  3. 修复代码逻辑错误
- 多轮迭代直到所有测试通过或达到最大迭代次数

## 配置建议

### 模型大小选择

| 模型大小 | 适用场景 | 速度 | 质量 |
|---------|---------|------|------|
| 7B | 快速原型、简单项目 | 快 | 一般 |
| 14B | 默认推荐、中等项目 | 中等 | 好 |
| 32B | 重要项目、复杂逻辑 | 较慢 | 很好 |
| 72B | 关键项目、最高质量 | 慢 | 最好 |

### 迭代次数建议

| 项目复杂度 | 推荐迭代次数 |
|-----------|------------|
| 简单（<500 行） | 3-5 |
| 中等（500-2000 行） | 5-8 |
| 复杂（>2000 行） | 8-15 |

## 常见问题

### Q: 编译修复一直失败怎么办？

A: 尝试以下方法：
1. 增加迭代次数：`--max-fix-iterations 15`
2. 使用更大的模型：`--model-size 32`
3. 检查 C 项目文档是否准确
4. 手动检查生成的代码

### Q: 测试修复失败但代码能编译？

A: 这是正常情况。测试修复失败可能是因为：
- 测试逻辑本身有问题
- 测试用例过于严格
- 实现与测试不匹配

可以：
1. 查看生成的测试代码
2. 手动调整测试用例
3. 使用 `--skip-test-fix` 跳过测试修复

### Q: 如何查看中间结果？

A: 所有中间结果都保存在输出目录：
- C 项目分析：`output/c_docs/`
- 迭代分析：`output/c_docs/iteration_*.md`
- 修复历史：Rust 项目目录中的日志

### Q: 可以只运行部分流程吗？

A: 可以，使用跳过参数：
```bash
# 跳过 C 分析
python agent/main.py ... --skip-c-analysis

# 跳过编译修复
python agent/main.py ... --skip-code-fix

# 跳过测试修复
python agent/main.py ... --skip-test-fix
```

## 性能优化

### 加速建议

1. **使用较小的模型**：`--model-size 7`
2. **减少迭代次数**：`--max-fix-iterations 3`
3. **跳过不必要的步骤**：`--skip-test-fix`

### 提高质量建议

1. **使用更大的模型**：`--model-size 32` 或 `72`
2. **增加迭代次数**：`--max-fix-iterations 10`
3. **保留所有修复步骤**（默认）

## 示例命令

### 示例 1：快速转换小项目

```bash
python agent/main.py datasets/small-project output/small-rust \
  --model-size 7 \
  --max-fix-iterations 3
```

### 示例 2：完整转换中等项目

```bash
python agent/main.py datasets/medium-project output/medium-rust \
  --rust-project-name medium_rs \
  --model-size 14 \
  --max-fix-iterations 5
```

### 示例 3：高质量转换大项目

```bash
python agent/main.py datasets/large-project output/large-rust \
  --rust-project-name large_rs \
  --model-size 32 \
  --max-fix-iterations 15
```

### 示例 4：仅生成代码（不修复）

```bash
python agent/main.py datasets/project output/project-rust \
  --skip-code-fix \
  --skip-test-fix
```

## 依赖要求

- Python 3.8+
- Rust toolchain (cargo, rustc)
- Qwen Local API 服务
- 必要的 Python 依赖包

## 注意事项

1. **备份原项目**：转换过程不会修改原 C 项目
2. **检查生成结果**：自动生成的代码可能需要人工审查
3. **测试覆盖率**：生成的测试可能不完整，建议补充
4. **性能考虑**：大项目和大模型会消耗较多时间

## 技术支持

如有问题，请检查：
1. 输出目录中的日志文件
2. C 项目分析文档是否准确
3. Rust 编译错误信息
4. 测试失败详情
