# md5-c 项目文档

## 项目概述

### 项目目的
本项目旨在实现一个符合RFC 1321标准的MD5消息摘要算法，用于数据完整性验证和密码存储。

### 主要功能
- 计算输入数据的MD5哈希值。
- 支持字符串和文件的哈希计算。

### 设计思路
使用分步处理的方式，允许逐步更新输入数据。包含初始化、更新和最终化三个主要步骤。

## 项目功能

### 计算MD5哈希值
- **md5String**: 计算字符串的MD5哈希值并存储在结果中。
- **md5File**: 计算文件内容的MD5哈希值并存储在结果中。

### 分步处理
- **md5Init**: 初始化MD5上下文。
- **md5Update**: 将输入数据添加到上下文中，并根据需要应用算法。
- **md5Finalize**: 填充当前输入，附加大小信息，并保存最终结果。

### 辅助函数
- **md5Step**: 对512位输入应用主MD5算法。

## 项目架构

### 模块划分
- **root 模块**
  - 包含 `md5.h` 和 `md5.c` 文件。

### 模块关系
- `main.c` 依赖于 `md5.h` 中定义的数据结构和函数。
- `md5.c` 内部使用了 `md5.h` 中定义的类型和函数。

## 技术特点

### 数据结构
- **MD5Context** [md5.h:10]
  - 字段：
    - `size`: 输入数据的总大小（以字节为单位）。
    - `buffer`: 当前累积的哈希值。
    - `input`: 用于下一次步骤的输入数据。
    - `digest`: 算法的结果。

### 关键函数
- **md5Init** [md5.c:30]
  - 参数：`MD5Context *ctx`
  - 返回值：无
  - 功能：初始化MD5上下文。

- **md5Update** [md5.c:35]
  - 参数：`MD5Context *ctx`, `uint8_t *input_buffer`, `size_t input_len`
  - 返回值：无
  - 功能：将输入数据添加到上下文中，并根据需要应用算法。

- **md5Finalize** [md5.c:65]
  - 参数：`MD5Context *ctx`
  - 返回值：无
  - 功能：填充当前输入，附加大小信息，并保存最终结果。

- **md5Step** [md5.c:95]
  - 参数：`uint32_t *buffer`, `uint32_t *input`
  - 返回值：无
  - 功能：对512位输入应用主MD5算法。

- **md5String** [md5.c:125]
  - 参数：`char *input`, `uint8_t *result`
  - 返回值：无
  - 功能：计算字符串的MD5哈希值并存储在结果中。

- **md5File** [md5.c:145]
  - 参数：`FILE *file`, `uint8_t *result`
  - 返回值：无
  - 功能：计算文件内容的MD5哈希值并存储在结果中。

## 代码结构和关键组件

### 数据结构
```c
// md5.h:10
typedef struct {
    uint64_t size;       // 输入数据的总大小（以字节为单位）
    uint32_t buffer[4];  // 当前累积的哈希值
    uint8_t input[64];   // 用于下一次步骤的输入数据
    uint8_t digest[16];  // 算法的结果
} MD5Context;
```

### 关键函数
```c
// md5.c:30
void md5Init(MD5Context *ctx) {
    ctx->size = 0;
    memset(ctx->buffer, 0, sizeof(ctx->buffer));
    memcpy(ctx->buffer, "\x67\x45\x23\x01\xef\xcd\xab\x89", 8);
    memcpy(ctx->buffer + 2, "\x98\xba\xd2\xc3\x10\x11\x12\x13", 8);
}

// md5.c:35
void md5Update(MD5Context *ctx, uint8_t *input_buffer, size_t input_len) {
    while (input_len > 0) {
        if (ctx->size % 64 == 0) {
            md5Finalize(ctx);
            md5Init(ctx);
        }
        size_t to_copy = MIN(64 - ctx->size % 64, input_len);
        memcpy(ctx->input + ctx->size % 64, input_buffer, to_copy);
        ctx->size += to_copy;
        input_buffer += to_copy;
        input_len -= to_copy;
    }
}

// md5.c:65
void md5Finalize(MD5Context *ctx) {
    uint8_t padding[64];
    padding[0] = 0x80;
    memset(padding + 1, 0, 63);

    if ((ctx->size % 64) <= 56) {
        memcpy(ctx->input + ctx->size % 64, padding, 56 - ctx->size % 64);
    } else {
        memcpy(ctx->input + ctx->size % 64, padding, 64 - ctx->size % 64);
        md5Update(ctx, NULL, 0);
        memset(ctx->input, 0, 56);
    }

    uint64_t bits = ctx->size * 8;
    memcpy(ctx->input + 56, &bits, 8);
    md5Step(ctx->buffer, (uint32_t *)ctx->input);
    memcpy(ctx->digest, ctx->buffer, 16);
}

// md5.c:95
void md5Step(uint32_t *buffer, uint32_t *input) {
    uint32_t a = buffer[0], b = buffer[1], c = buffer[2], d = buffer[3];

    #define F(x,y,z) ((z) ^ ((x) & (y) | ~(x)))
    #define G(x,y,z) ((y) ^ ((x) & (z) | ~(z)))
    #define H(x,y,z) ((x) ^ (y) ^ (z))
    #define I(x,y,z) ((y) ^ ((x) | ~(z)))

    #define FF(a,b,c,d,x,s,t) { a += F(b,c,d) + x + t; a = a << s | a >> (32-s); a += b; }
    #define GG(a,b,c,d,x,s,t) { a += G(b,c,d) + x + t; a = a << s | a >> (32-s); a += b; }
    #define HH(a,b,c,d,x,s,t) { a += H(b,c,d) + x + t; a = a << s | a >> (32-s); a += b; }
    #define II(a,b,c,d,x,s,t) { a += I(b,c,d) + x + t; a = a << s | a >> (32-s); a += b; }

    for (int i = 0; i < 64; i++) {
        uint32_t temp = buffer[0];
        switch (i / 16) {
            case 0: FF(buffer[1], buffer[2], buffer[3], input[i], 7, 0xd76aa478); break;
            case 1: GG(buffer[2], buffer[3], buffer[0], input[i], 12, 0xe8c7b756); break;
            case 2: HH(buffer[3], buffer[0], buffer[1], input[i], 17, 0x242070db); break;
            case 3: II(buffer[0], buffer[1], buffer[2], input[i], 22, 0xc1bdceee); break;
            case 4: FF(buffer[1], buffer[2], buffer[3], input[i], 7, 0xf57c0faf); break;
            case 5: GG(buffer[2], buffer[3], buffer[0], input[i], 12, 0x4787c62a); break;
            case 6: HH(buffer[3], buffer[0], buffer[1], input[i], 17, 0xa8304613); break;
            case 7: II(buffer[0], buffer[1], buffer[2], input[i], 22, 0xfd469501); break;
            case 8: FF(buffer[1], buffer[2], buffer[3], input[i], 7, 0x698098d8); break;
            case 9: GG(buffer[2], buffer[3], buffer[0], input[i], 12, 0x8b44f7af); break;
            case 10: HH(buffer[3], buffer[0], buffer[1], input[i], 17, 0xffff5bb1); break;
            case 11: II(buffer[0], buffer[1], buffer[2], input[i], 22, 0x895cd7be); break;
            case 12: FF(buffer[1], buffer[2], buffer[3], input[i], 7, 0x6b901122); break;
            case 13: GG(buffer[2], buffer[3], buffer[0], input[i], 12, 0xfd987193); break;
            case 14: HH(buffer[3], buffer[0], buffer[1], input[i], 17, 0xa679438e); break;
            case 15: II(buffer[0], buffer[1], buffer[2], input[i], 22, 0x49b40821); break;
        }
        buffer[1] = buffer[1] + buffer[2];
        buffer[2] = buffer[2] + buffer[3];
        buffer[3] = buffer[3] + buffer[0];
        buffer[0] = buffer[0] + temp;
    }
}

// md5.c:125
void md5String(char *input, uint8_t *result) {
    MD5Context ctx;
    md5Init(&ctx);
    md5Update(&ctx, (uint8_t *)input, strlen(input));
    md5Finalize(&ctx);
    memcpy(result, ctx.digest, 16);
}

// md5.c:145
void md5File(FILE *file, uint8_t *result) {
    MD5Context ctx;
    md5Init(&ctx);
    uint8_t buffer[1024];
    size_t bytes_read;
    while ((bytes_read = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        md5Update(&ctx, buffer, bytes_read);
    }
    fclose(file);
    md5Finalize(&ctx);
    memcpy(result, ctx.digest, 16);
}
```

### 模块关系图
```
+-------------------+
|      main.c         |
+---------+---------+
          |
          v
+---------+---------+
|      md5.c          |
+-------------------+
```

### 总结
通过上述文档，我们详细地介绍了 `md5-c` 项目的各个方面，包括项目目的、主要功能、设计思路、代码结构和关键组件。希望这份文档能够帮助开发者更好地理解和使用这个项目。