# libfuse_sshfs 测例适配调查报告

调查日期：2026-05-19

## 结论

`libfuse_sshfs` 可以编译出可执行文件 `sshfs`，也自带测试，但不适合作为当前翻译项目的常规测例。它的测试依赖 FUSE、`/dev/fuse`、`fusermount`、本机 SSH 免密登录、pytest、Meson/Ninja 和系统挂载能力。失败原因很容易来自系统环境，而不是翻译结果。

如果将它纳入 benchmark，建议单独放入「系统集成型测试」分组，并要求测试环境预先配置 FUSE 和 localhost SSH。对于当前「C 可执行文件 + sh 对比 Rust 可执行文件」模型，它的适配成本偏高。

适合度：低。  
推荐优先级：暂不纳入主评测集。  
是否适合当前「C 可执行文件 + sh 对比 Rust 可执行文件」模型：不适合常规批量测试。

## 项目概况

- 项目路径：`datasets/libfuse_sshfs`
- 主程序入口：`sshfs.c`
- 辅助源文件：`cache.c`
- 代码规模：约 4,767 行 C/H 文件
- 构建方式：Meson + Ninja
- 默认产物：`sshfs`
- 测试目录：`test/`
- 测试形式：pytest + FUSE 集成测试

## 构建方式

项目使用 Meson：

```bash
mkdir build
cd build
meson ..
ninja
```

主要依赖：

- `fuse3 >= 3.1.0`
- `glib-2.0`
- `gthread-2.0`
- Meson
- Ninja

`meson.build` 中明确生成：

```meson
executable('sshfs', sshfs_sources, ...)
```

## 现有测试结构

测试目录包含：

- `test_sshfs.py`
- `util.py`
- `conftest.py`
- `pytest.ini`
- `wrong_command.c`
- CI 辅助脚本

README 推荐测试方式：

```bash
python3 -m pytest test/
```

测试不是普通 sh，而是 Python pytest。`test_sshfs.py` 会启动 `sshfs`，挂载本地临时目录，并执行文件系统操作。

## 测试覆盖内容

现有测试覆盖真实 SSHFS 文件系统行为，包括：

- 挂载和卸载。
- `statvfs`。
- `readdir`。
- open/read/write。
- append。
- seek。
- create。
- mkdir/rmdir。
- unlink。
- symlink。
- chown（root 环境下）。
- utimens。
- hard link。
- truncate。
- open 后 unlink。
- cache timeout 不同配置。
- debug/sync readdir/multiconn 组合。

覆盖很全面，但几乎全部是系统集成行为。

## 主要风险

### FUSE 环境要求高

测试会检查：

- `fusermount` 是否存在。
- `/dev/fuse` 是否存在。
- 非 root 用户下 `fusermount` 是否 setuid。
- 当前用户是否能打开 `/dev/fuse`。

容器、WSL、CI、普通 Linux 用户环境都可能不满足。

### 依赖 localhost SSH 免密登录

`test_sshfs.py` 会执行：

```bash
ssh localhost -- true
```

如果本机没有 sshd、没有免密 key、或禁止 localhost 登录，测试会失败。

### 测试不适合简单 stdout 对比

SSHFS 的正确性体现为挂载后的文件系统语义，不是命令输出。我们的 test agent 当前更适合 CLI 输出对比，这类测试需要特殊 runner。

### Rust 翻译目标复杂

Rust 版本需要实现 FUSE 文件系统和 SFTP/SSH 交互。即使编译通过，功能修复也会涉及系统调用、异步进程、挂载生命周期和权限问题，调试成本高。

## 可行的手动改造方向

如果必须使用该项目，可以只保留非常小的 smoke 测试：

1. `sshfs --help` 输出。
2. 参数错误时的 stderr 和 exit code。
3. `sshfs` 对非法 mount 参数的错误处理。

这类测试可以写成 sh，并适配 C/Rust 二进制对比。但它们无法证明 SSHFS 核心功能正确。

如果要验证核心功能，需要固定环境：

1. Linux 主机或支持 FUSE 的容器。
2. 安装 `fuse3`、`glib2`、`pytest`。
3. 配置 localhost sshd 和免密登录。
4. 用 pytest 或手写 sh 挂载临时目录。
5. 测试结束必须可靠卸载。

## 对 Rust 翻译项目的价值

该项目能测试非常高阶的系统能力：

- FUSE 回调。
- 文件系统语义。
- 子进程和 SSH/SFTP 协议。
- cache 行为。
- 错误码映射。

但这些能力超过了当前普通 CLI 翻译 benchmark 的范围。

## 最终建议

暂不纳入主评测集。可以保留为后续「系统集成型项目」的压力测试，但不适合作为 100 个项目中的普通自动测例。若一定要纳入，只建议先写 `--help`、非法参数、版本信息等 smoke sh，不建议直接迁移完整 pytest。
