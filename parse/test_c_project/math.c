#include "math.h"

// 计算平方
int calculate_square(int value) {
    return value * value;
}

// 计算平均值
double calculate_average(int *values, int count) {
    if (count == 0) {
        return 0.0;
    }
    
    int sum = 0;
    for (int i = 0; i < count; i++) {
        sum += values[i];
    }
    
    return (double)sum / count;
}
