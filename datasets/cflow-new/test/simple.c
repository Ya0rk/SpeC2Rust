/* simple.c - 简单的函数调用示例 */
int add(int a, int b) {
    return a + b;
}

int mul(int x, int y) {
    return x * y;
}

int orphan(void) {
    return 42;
}

int compute(int n) {
    int s = 0;
    for (int i = 0; i < n; i++)
        s = add(s, mul(i, i));
    return s;
}

int main(int argc, char **argv) {
    return compute(10);
}
