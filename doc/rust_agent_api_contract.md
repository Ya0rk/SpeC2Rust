# RustAgent 接口契约补充说明

## 目标

为 `RustAgent` 增加一层轻量的机器可读接口事实，减少多文件独立生成时的接口漂移问题。

## 持久化文件

Rust 项目根目录下会新增：

- `.cgr_api_contract.json`

该文件记录当前已生成 Rust 文件中抽取出的接口事实。

## 当前抽取内容

当前是启发式抽取，重点覆盖：

- `pub struct`
  - 结构体名
  - 字段名
  - 字段是否 `pub`
  - 字段类型
- `pub enum`
  - 枚举名
  - 变体名
- `pub trait`
  - trait 名
  - 方法名
- `pub fn`
  - 顶层公开函数
- `impl`
  - 公开方法
  - 构造函数（`new`、`with_*`）
  - 常见 getter / accessor（如 `x`、`y`、`root`、`bounds`、`state`、`config`）

## RustAgent 如何使用

每次：

1. 跳过并复用已有文件时
2. 成功写入新的 `.rs` 文件后

都会更新 `.cgr_api_contract.json`。

后续生成新文件时，`RustAgent` 会把当前契约摘要拼进上下文，提醒模型：

- 某个结构体字段是 `pub` 还是 `private`
- 某个访问方式是字段还是 getter
- 某个类型已经有哪些构造器
- 某个枚举已经有哪些变体

## 预期收益

这层契约不能替代完整 AST，但能明显降低这类跨文件错误：

- 把 private field 当 public field 访问
- 把 getter 当字段使用
- 构造器签名不一致
- enum variant 名字漂移
- 某个 `with_*` / `new` API 在不同文件里各写一套

## 当前边界

这是轻量版本，不保证：

- 泛型约束完整
- 生命周期完整
- trait object 细节完整
- 宏展开后的接口完整

如果后续需要更强一致性，可以再升级成：

- 基于 Rust AST 的结构化抽取
- 针对核心模块的显式 `api_contract.json` 生成与校验
