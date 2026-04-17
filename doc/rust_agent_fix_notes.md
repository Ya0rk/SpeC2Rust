# RustAgent 修复记录

更新时间：2026-04-14

## 本次修复目标

针对 `src/agent/rust_agent.py` 中两个高优先级问题做最小改动修复：

1. 依赖检测误作用于 `README.md` 等非 Rust 源文件，导致 `Cargo.toml` 被无关文本污染。
2. `Cargo.toml` 自动更新逻辑过于粗糙，只靠字符串追加，容易写坏 manifest。

## 具体修改

### 1. 限制依赖检测的触发范围

新增：

- `RustAgent._should_detect_dependencies(file_path)`

行为：

- 仅对 `.rs` 文件执行依赖检测
- 明确排除：
  - `Cargo.toml`
  - `README.md`

效果：

- `README.md` 中的示例代码、注释中的 `use xxx`、文档片段不再误触发 `Cargo.toml` 更新

### 2. 将 Cargo.toml 更新改为“段内合并”

新增：

- `RustAgent._ensure_dependencies_section(content)`
- `RustAgent._merge_dependencies_into_toml(content, dependencies)`

新的更新策略：

- 先确保存在 `[dependencies]`
- 只解析并修改 `[dependencies]` 段
- 只在该段内部判断依赖是否已存在
- 仅补充缺失项，不再全文件模糊查重
- 写回前使用 `tomllib.loads(...)` 做 TOML 校验

效果：

- 降低误判已有依赖的概率
- 避免把依赖写到错误段落
- 在写坏 manifest 前提前失败

## 设计取舍

这次没有引入第三方 TOML 写库，也没有重写整个 manifest 生成器，原因是：

- 需要保持最小改动
- 需要兼容现有文件内容和注释
- 当前目标是先修正高风险污染点，而不是重构整套配置写入系统

## 后续建议

如果后面还要继续增强，优先级建议如下：

1. 将 `_detect_dependencies()` 的规则从字符串匹配升级为更稳的 Rust `use`/attribute 检测
2. 对 `[dev-dependencies]`、`[features]` 增加结构化更新能力
3. 控制 `generate_code()` 中的上下文滚雪球，减少后续文件被前面膨胀代码带偏
