# blechschmidt_massdns 测例适配调查报告

调查日期：2026-05-19

## 结论

`blechschmidt_massdns` 可以编译出命令行可执行文件 `bin/massdns`，也自带 sh 测试，但不建议直接作为第一批稳定测例。主要原因是现有测试依赖真实 DNS 网络、公共解析器、IPv6 环境和 `jq`。这些因素会让 C 参考实现与 Rust 实现的差异被网络波动掩盖。

如果允许手动改造，推荐把它放入「网络型集成测试」候选集，而不是默认功能回归集。更合理的改造方式是引入本地 DNS mock server 或固定 UDP/TCP 响应 fixture，让测试只验证 `massdns` 的输入解析、DNS 报文编解码、输出格式和错误码处理。

适合度：中等偏低。  
推荐优先级：第三批。  
是否适合当前「C 可执行文件 + sh 对比 Rust 可执行文件」模型：有条件适合。

## 项目概况

- 项目路径：`datasets/blechschmidt_massdns`
- 主程序入口：`src/main.c`
- 代码规模：约 6,490 行 C/H 文件
- 构建方式：`make` 或 CMake
- 默认产物：`bin/massdns`
- 测试目录：`tests/`
- 测试脚本数量：7 个 sh 脚本（含总入口 `tests/run-tests.sh`）

## 构建方式

项目根目录的 `Makefile` 会直接编译 `src/main.c`：

```bash
make
```

默认目标会启用 Linux 相关宏：

- `HAVE_EPOLL`
- `HAVE_SYSINFO`
- `-std=c11`
- `-O3`

非 Linux 环境可以使用：

```bash
make nolinux
```

CMake 也能生成同名产物，且会根据平台检测 `sys/epoll.h`、`sys/ioctl.h` 并设置宏。对我们的翻译流程来说，`Makefile` 更直接，因为它清楚地表明项目主要产物就是一个 CLI 二进制。

## 现有测试结构

现有测试位于 `tests/` 下，每个子目录包含：

- `run.sh`
- `names.txt`
- `google-dns.txt`
- 部分测试包含 `expected`

总入口：

```bash
tests/run-tests.sh
```

典型测试命令：

```bash
../../bin/massdns -c 3 --quiet -r google-dns.txt names.txt | grep -E -q "$(cat expected)"
```

部分测试会使用 JSON 输出并依赖 `jq`：

```bash
../../bin/massdns --extended-input -c 50 -r google-dns.txt --quiet -o J names.txt \
  | jq -r .resolver \
  | grep -E -q "$(cat expected)"
```

## 测试覆盖内容

现有测试覆盖了这些行为：

- 从文件读取域名列表。
- 从 stdin 管道读取域名列表。
- 使用 resolver 文件发起 DNS 查询。
- IPv4 resolver。
- IPv6 resolver。
- `--extended-input` 扩展输入模式。
- `--ignore NOERROR` / `--ignore NXDOMAIN`。
- 文本输出与 JSON 输出格式。

这些测试对 CLI 行为有价值，但更像真实网络集成测试，不是纯功能单元测试。

## 主要风险

### 网络依赖

测试使用公共 DNS，例如 `8.8.8.8`、`8.8.4.4`、`1.1.1.1` 和 `2001:4860:4860::8888`。这会带来以下风险：

- CI 或本地环境可能无法访问外网 DNS。
- UDP 53 可能被网络策略拦截。
- IPv6 测试依赖机器和网络支持 IPv6。
- 公共 resolver 可能限速、超时或返回不同 TTL。

### 输出存在天然不稳定字段

`expected` 中已经用正则规避了一些字段，例如：

- DNS ID
- Unix time
- TTL
- 响应大小

但真实 DNS 响应仍可能受 resolver 行为影响。

### 依赖外部工具

JSON 测试依赖 `jq`。如果测试环境没有 `jq`，测试会因为环境失败，而不是因为翻译错误失败。

## 适配建议

### 推荐方案：本地 DNS mock

为该项目写新的 sh 测试时，建议启动一个本地 DNS server：

1. 监听 `127.0.0.1:5353` 或随机高位端口。
2. 对固定域名返回固定 A/AAAA/NXDOMAIN 响应。
3. resolver 文件写成本地地址。
4. 同一组输入分别跑 C 二进制和 Rust 二进制。
5. 比较规范化后的输出。

这种方案能把测试目标从「公网 DNS 是否可用」收缩到「程序行为是否一致」。

### 可保留的测试点

- `names.txt` 文件输入。
- stdin 管道输入。
- `--extended-input`。
- `-o J` JSON 输出。
- `--ignore`。
- `-t A` / `-t AAAA`。
- timeout 与失败状态码。

### 不建议直接保留的测试点

- 直接访问 Google DNS / Cloudflare DNS。
- 强依赖 IPv6 公网。
- 依赖当前时间、随机 DNS ID 的精确输出。

## 对 Rust 翻译项目的价值

该项目能测试以下能力：

- 网络 I/O。
- DNS 协议编解码。
- CLI 参数解析。
- 大量结构体和状态机。
- stdin / 文件输入。
- 文本与 JSON 输出。

它对 Rust 化重构有价值，但对自动评测环境不友好。建议在基础 CLI 项目稳定后再纳入。

## 最终建议

不要直接复制现有 `tests/` 作为评测脚本。建议先手写一组本地 DNS mock 驱动的 sh 测试，再把它纳入 benchmark。否则测试失败很难判断是翻译错误、网络错误还是系统依赖错误。
