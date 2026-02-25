#ifndef UTILS_H
#define UTILS_H

#include <stdio.h>

// 内联函数宏定义
#define INLINE static inline

// 普通函数声明
void print_message(const char *message);

// 内联函数
INLINE int add_numbers(int a, int b) {
    return a + b;
}

// 带函数体的声明（测试内联函数解析）
INLINE int multiply_numbers(int a, int b) {
    return a * b;
}

#endif // UTILS_H
