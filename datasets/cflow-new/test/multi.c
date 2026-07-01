/* multi.c - 多文件测试 (通过 #include 组合) */
#include <stdio.h>

static void helper(void) {
    printf("helper\n");
}

static int twice(int x) {
    return x * 2;
}

void run(void) {
    helper();
    int r = twice(42);
    printf("%d\n", r);
}
