#include <stdio.h>
#include "utils.h"
#include "math.h"

// 全局变量
int global_counter = 0;

// 普通函数
void process_data(int value) {
    printf("Processing data: %d\n", value);
    global_counter++;
    
    // 调用其他函数
    int result = calculate_square(value);
    printf("Square result: %d\n", result);
    
    print_message("Data processing completed");
}

// 主函数
int main() {
    printf("Test C Project\n");
    printf("Global counter initial: %d\n", global_counter);
    
    // 测试不同功能
    process_data(5);
    process_data(10);
    
    printf("Global counter final: %d\n", global_counter);
    
    // 测试内联函数
    int sum = add_numbers(3, 7);
    printf("Sum: %d\n", sum);
    
    return 0;
}
