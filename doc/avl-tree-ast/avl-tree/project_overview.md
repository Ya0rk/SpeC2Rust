# 项目总文档

## 1. 项目概述

本项目名为 `avl-tree`，旨在实现一个高效的自平衡二叉查找树（AVL Tree）。AVL树是一种特殊的二叉查找树，它通过在每个节点上增加一个平衡因子来保证树的平衡性，从而使得查找、插入和删除操作的时间复杂度均为 O(log n)。

## 2. 项目功能

### 主要功能
- **插入操作**：将新元素插入到AVL树中，并保持树的平衡。
- **删除操作**：从AVL树中删除指定元素，并保持树的平衡。
- **查找操作**：根据给定键查找元素。
- **遍历操作**：支持前序、中序和后序遍历。
- **打印操作**：以树形结构打印AVL树。
- **检查操作**：检查树是否满足AVL树的性质（即所有节点的左右子树高度差不超过1）。

### 关键函数
- `avl_create`：创建一个新的AVL树实例。
  - 参数：`compare_func`（比较函数）、`destroy_func`（销毁函数）。
  - 返回值：指向新创建的AVL树实例的指针。
  - [src/avl_tree.c:42]

- `avl_destroy`：销毁AVL树及其所有节点。
  - 参数：`avlt`（AVL树实例）。
  - 返回值：无。
  - [src/avl_tree.c:89]

- `avl_find`：在AVL树中查找指定键的节点。
  - 参数：`avlt`（AVL树实例）、`data`（要查找的数据）。
  - 返回值：找到的节点指针，未找到则返回NULL。
  - [src/avl_tree.c:127]

- `avl_successor`：获取指定节点的后继节点。
  - 参数：`avlt`（AVL树实例）、`node`（当前节点）。
  - 返回值：后继节点指针，未找到后继节点则返回NULL。
  - [src/avl_tree.c:165]

- `avl_insert`：将新元素插入到AVL树中。
  - 参数：`avlt`（AVL树实例）、`data`（要插入的数据）。
  - 返回值：插入的新节点指针，插入失败则返回NULL。
  - [src/avl_tree.c:203]

- `avl_delete`：从AVL树中删除指定节点。
  - 参数：`avlt`（AVL树实例）、`node`（要删除的节点）、`keep`（是否保留数据）。
  - 返回值：被删除节点的数据指针，删除失败则返回NULL。
  - [src/avl_tree.c:241]

- `avl_apply`：对AVL树进行遍历并应用指定函数。
  - 参数：`avlt`（AVL树实例）、`node`（当前节点）、`func`（应用函数）、`cookie`（用户数据）、`order`（遍历顺序）。
  - 返回值：非零表示错误发生。
  - [src/avl_tree.c:279]

- `avl_print`：以树形结构打印AVL树。
  - 参数：`avlt`（AVL树实例）、`print_func`（打印函数）。
  - 返回值：无。
  - [src/avl_tree.c:317]

- `avl_check_order`：检查AVL树是否满足键的有序性。
  - 参数：`avlt`（AVL树实例）、`min`（最小键）、`max`（最大键）。
  - 返回值：1表示满足，0表示不满足。
  - [src/avl_tree.c:355]

- `avl_check_height`：检查AVL树的高度是否满足AVL树的性质。
  - 参数：`avlt`（AVL树实例）。
  - 返回值：1表示满足，0表示不满足。
  - [src/avl_tree.c:393]

### 关键数据结构
- `avlnode`：AVL树的节点结构体。
  - 字段：`left`（左子节点）、`right`（右子节点）、`parent`（父节点）、`bf`（平衡因子）、`data`（存储的数据）。
  - [src/avl_node.h:12]

- `avltree`：AVL树的根结构体。
  - 字段：`compare`（比较函数）、`destroy`（销毁函数）、`root`（根节点）、`nil`（哨兵节点）、`min`（最小节点指针）。
  - [src/avl_tree.h:12]

## 3. 项目架构

项目采用模块化设计，主要由以下几个模块组成：
- `root`：主模块，负责AVL树的基本操作和管理。
- `avl_data.c`：提供数据相关的辅助函数。
- `minunit.h`：用于单元测试的框架。

## 4. 模块关系

- `root` 模块依赖于 `avl_data.c` 模块中的比较和销毁函数。
- `root` 模块依赖于 `minunit.h` 模块进行单元测试。

## 5. 技术特点

- **平衡性**：通过维护每个节点的平衡因子，确保树在每次插入和删除操作后都能自动调整，保持树的平衡。
- **灵活性**：提供多种遍历方式和自定义函数应用接口，方便用户根据需要进行扩展和定制。
- **健壮性**：通过断言和错误处理机制，确保在出现异常情况时能够及时发现并处理。

## 6. 使用说明

### 安装依赖
确保系统已经安装了必要的开发工具和库，如GCC编译器和Makefile。

### 编译项目
在项目根目录下运行以下命令进行编译：
```sh
make
```

### 运行测试
在项目根目录下运行以下命令进行单元测试：
```sh
./test_avl_tree
```

### 示例代码
以下是一个简单的示例代码，展示如何使用 `avl-tree` 库：
```c
#include "avl_tree.h"
#include <stdio.h>

int compare(const void *a, const void *b) {
    return (*(int *)a - *(int *)b);
}

void destroy(void *data) {
    free(data);
}

void print(int *data) {
    printf("%d ", *data);
}

int main() {
    avltree *tree = avl_create(compare, destroy);
    if (!tree) {
        fprintf(stderr, "Failed to create AVL tree\n");
        return 1;
    }

    int data[] = {10, 20, 30, 40, 50};
    for (int i = 0; i < 5; i++) {
        int *new_data = malloc(sizeof(int));
        *new_data = data[i];
        avlnode *node = avl_insert(tree, new_data);
        if (!node) {
            fprintf(stderr, "Failed to insert %d into AVL tree\n", data[i]);
            avl_destroy(tree);
            return 1;
        }
    }

    avl_print(tree, print);
    printf("\n");

    avl_destroy(tree);
    return 0;
}
```

### 编译和运行示例代码
在项目根目录下创建一个名为 `example.c` 的文件，并将上述示例代码粘贴进去。然后运行以下命令进行编译和运行：
```sh
gcc example.c src/avl_tree.c src/avl_node.c -o example
./example
```

这将输出：
```
10 20 30 40 50 
```

通过以上步骤，您可以成功编译和运行 `avl-tree` 项目，并了解其基本功能和使用方法。