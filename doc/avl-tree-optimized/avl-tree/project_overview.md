# 项目总文档

## 1. 项目概述

本项目名为 `avl-tree`，旨在实现一个自平衡二叉查找树（AVL Tree）。AVL树是一种特殊的二叉查找树，它通过在每个节点上增加一个平衡因子来保持树的平衡，从而保证所有操作的时间复杂度为 O(log n)。

## 2. 项目功能

### 主要功能
- **插入操作**：将新元素插入到AVL树中，并保持树的平衡。
- **删除操作**：从AVL树中删除指定元素，并保持树的平衡。
- **查找操作**：根据给定键查找树中的元素。
- **打印操作**：以特定格式打印AVL树的内容。
- **检查操作**：验证AVL树是否满足平衡条件。

### 关键函数
- `avl_create`：创建一个新的AVL树实例。
  - 参数：`compare_func`（比较函数）、`destroy_func`（销毁函数）。
  - 返回值：指向新创建的AVL树实例的指针。
  - [src/avl_tree.h:10]

- `avl_destroy`：销毁AVL树及其所有节点。
  - 参数：`avlt`（AVL树实例）。
  - 返回值：无。
  - [src/avl_tree.h:20]

- `avl_find`：在AVL树中查找指定键的节点。
  - 参数：`avlt`（AVL树实例）、`data`（要查找的数据）。
  - 返回值：找到的节点指针，未找到则返回NULL。
  - [src/avl_tree.h:30]

- `avl_successor`：获取指定节点的后继节点。
  - 参数：`avlt`（AVL树实例）、`node`（当前节点）。
  - 返回值：后继节点指针，未找到则返回NULL。
  - [src/avl_tree.h:40]

- `avl_insert`：将新元素插入到AVL树中。
  - 参数：`avlt`（AVL树实例）、`data`（要插入的数据）。
  - 返回值：插入的节点指针，失败时返回NULL。
  - [src/avl_tree.h:50]

- `avl_delete`：从AVL树中删除指定节点。
  - 参数：`avlt`（AVL树实例）、`node`（要删除的节点）、`keep`（是否保留数据）。
  - 返回值：被删除节点的数据指针，失败时返回NULL。
  - [src/avl_tree.h:60]

- `avl_apply`：对AVL树进行遍历并应用指定函数。
  - 参数：`avlt`（AVL树实例）、`node`（当前节点）、`func`（应用函数）、`cookie`（用户数据）、`order`（遍历顺序）。
  - 返回值：非零表示错误。
  - [src/avl_tree.h:70]

- `avl_print`：打印AVL树的内容。
  - 参数：`avlt`（AVL树实例）、`print_func`（打印函数）。
  - 返回值：无。
  - [src/avl_tree.h:80]

- `avl_check_order`：检查AVL树是否满足键的顺序要求。
  - 参数：`avlt`（AVL树实例）、`min`（最小键）、`max`（最大键）。
  - 返回值：1表示满足，0表示不满足。
  - [src/avl_tree.h:90]

- `avl_check_height`：检查AVL树的高度是否满足平衡条件。
  - 参数：`avlt`（AVL树实例）。
  - 返回值：1表示满足，0表示不满足。
  - [src/avl_tree.h:100]

### 关键数据结构
- `avlnode`：AVL树的节点结构体。
  - 字段：`left`（左子节点）、`right`（右子节点）、`parent`（父节点）、`bf`（平衡因子）、`data`（存储的数据）。
  - [src/avl_node.h:10]

- `avltree`：AVL树的根结构体。
  - 字段：`compare`（比较函数）、`destroy`（销毁函数）、`nil`（哨兵节点）、`root`（根节点）、`min`（最小节点）。
  - [src/avl_tree.h:20]

## 3. 项目架构

项目分为以下几个模块：
- `root`：主模块，负责初始化和销毁AVL树。
- `avl_bf.c`：包含AVL树的基本操作函数，如创建、销毁、查找、插入、删除等。
- `avl_data.c`：包含数据类型的定义和一些辅助函数，如创建数据节点、比较数据节点等。

## 4. 模块关系

- `avl_example.c`模块使用了`avl_bf.c`和`avl_data.c`模块提供的函数来创建、插入、删除和打印AVL树。
- `avl_bf.c`模块提供了AVL树的基本操作函数，如创建、销毁、查找、插入、删除等。
- `avl_data.c`模块提供了数据类型的定义和一些辅助函数，如创建数据节点、比较数据节点等。

## 5. 技术特点

- **自平衡**：通过平衡因子记录每个节点的左右子树高度差，确保树始终平衡。
- **哨兵节点**：使用哨兵节点简化边界情况处理。
- **多种遍历方法**：提供前序、中序、后序等多种遍历方法，方便用户对树进行各种操作。
- **灵活的数据类型**：支持任意数据类型，只需提供相应的比较和销毁函数。

## 6. 使用说明

### 安装依赖
确保系统已安装必要的编译工具和库。

### 编译项目
```sh
make
```

### 运行示例程序
```sh
./avl_example
```

### 示例代码
以下是一个简单的示例代码，展示如何使用AVL树：

```c
#include "avl_tree.h"
#include <stdio.h>
#include <stdlib.h>

int compare(const void *a, const void *b) {
    return (*(int *)a - *(int *)b);
}

void destroy(void *data) {
    free(data);
}

int main() {
    avltree *tree = avl_create(compare, destroy);

    int data[] = {10, 20, 30, 40, 50};
    for (int i = 0; i < 5; i++) {
        avlnode *node = avl_insert(tree, &data[i]);
        if (!node) {
            fprintf(stderr, "Insert failed\n");
            return 1;
        }
    }

    avl_print(tree, printf);

    avlnode *found = avl_find(tree, &data[2]);
    if (found) {
        printf("Found node with value %d\n", *(int *)found->data);
    } else {
        printf("Node not found\n");
    }

    avlnode *deleted = avl_delete(tree, found, 1);
    if (deleted) {
        printf("Deleted node with value %d\n", *(int *)deleted->data);
        free(deleted->data);
        free(deleted);
    }

    avl_destroy(tree);

    return 0;
}
```

### 注意事项
- 确保在使用完AVL树后调用`avl_destroy`函数，以释放所有分配的内存。
- 在插入和删除操作时，注意检查返回值，以确保操作成功。

通过以上文档，您可以全面了解 `avl-tree` 项目的各个方面，包括其功能、架构、模块关系和技术特点。希望这些信息对您有所帮助！