# 项目总文档

## 1. 项目概述

项目名称：md5-c

项目描述：md5-c 是一个实现 MD5 消息摘要算法的 C 语言项目。该算法遵循 RFC 1321 标准，广泛应用于数据完整性验证、密码存储等领域。

## 2. 项目功能

该项目的主要功能是计算输入数据的 MD5 哈希值。具体功能如下：

- 计算字符串的 MD5 哈希值。
- 计算文件内容的 MD5 哈希值。
- 支持分步处理输入数据，适用于大文件或流式数据。

## 3. 项目架构

项目分为以下几个模块：

- **root 模块**：根目录，包含项目入口和其他配置文件。
- **md5 模块**：包含 MD5 算法的具体实现。
- **main 模块**：包含程序的入口点和示例代码。

## 4. 模块关系

### root 模块

- **依赖关系**：无直接依赖。
- **调用关系**：由 `main.c` 调用。

### md5 模块

- **数据结构**：
  - `MD5Context` [md5.h:10]

- **关键函数**：
  - `md5Init` [md5.c:30]
  - `md5Update` [md5.c:35]
  - `md5Finalize` [md5.c:65]
  - `md5Step` [md5.c:95]
  - `md5String` [md5.c:125]
  - `md5File` [md5.c:145]

### main 模块

- **依赖关系**：依赖于 `md5.h` 中定义的数据结构和函数。
- **调用关系**：
  - 调用 `md5String` 或 `md5File` 来计算输入数据的 MD5 哈希值。
  - 这些函数内部会调用 `md5Init`, `md5Update`, 和 `md5Finalize` 来完成实际的计算过程。

## 5. 技术特点

- **符合标准**：严格遵循 RFC 1321 标准实现 MD5 算法。
- **高效实现**：使用位操作和循环优化算法性能。
- **分步处理**：支持分步处理输入数据，适用于大文件或流式数据。
- **易用接口**：提供简洁的 API 接口，方便用户调用。

## 6. 使用说明

### 安装

1. 克隆项目仓库：
   ```sh
   git clone https://github.com/your-repo/md5-c.git
   ```

2. 进入项目目录：
   ```sh
   cd md5-c
   ```

3. 编译项目：
   ```sh
   make
   ```

### 示例代码

以下是一个简单的示例代码，展示如何使用 `md5String` 和 `md5File` 函数：

```c
#include <stdio.h>
#include "md5.h"

int main() {
    // 计算字符串的 MD5 哈希值
    char input[] = "Hello, World!";
    uint8_t result[16];
    md5String(input, result);

    printf("MD5 of '%s': ", input);
    for (int i = 0; i < 16; i++) {
        printf("%02x", result[i]);
    }
    printf("\n");

    // 计算文件内容的 MD5 哈希值
    FILE *file = fopen("example.txt", "rb");
    if (file) {
        md5File(file, result);
        fclose(file);

        printf("MD5 of 'example.txt': ");
        for (int i = 0; i < 16; i++) {
            printf("%02x", result[i]);
        }
        printf("\n");
    } else {
        perror("Failed to open file");
    }

    return 0;
}
```

### 编译和运行

编译示例代码：
```sh
gcc -o example example.c md5.c
```

运行示例代码：
```sh
./example
```

输出示例：
```
MD5 of 'Hello, World!': ed076287532e86365e841e92bfc50d8c
MD5 of 'example.txt': ...
```

通过以上步骤，您可以成功编译和运行项目，并使用提供的示例代码进行 MD5 哈希值的计算。