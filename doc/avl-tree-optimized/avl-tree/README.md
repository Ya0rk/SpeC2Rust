# AVL Tree Project

## 项目简介
AVL树是一种自平衡二叉查找树，它在每个节点上都保存了其左右子树的高度差，并且保证这个高度差不超过1。这种平衡性使得AVL树在最坏情况下的时间复杂度为O(log n)，适用于需要频繁插入和删除操作的数据结构。

## 功能特性
- 支持基本的插入、删除和查找操作。
- 自动维护树的平衡状态，确保每次操作后树仍然保持平衡。
- 提供高效的遍历方式，如中序遍历、前序遍历和后序遍历。

## 安装与使用
### 安装
该项目是一个纯C语言实现，无需额外依赖库即可编译运行。只需将源代码文件复制到你的项目目录中即可。

### 使用
1. 包含头文件：在你的C程序中包含`avl_tree.h`头文件。
2. 初始化树：使用`avl_init()`函数初始化一个新的AVL树。
3. 插入元素：使用`avl_insert()`函数向树中插入元素。
4. 查找元素：使用`avl_search()`函数在树中查找元素。
5. 删除元素：使用`avl_delete()`函数从树中删除元素。
6. 遍历树：使用`avl_inorder_traverse()`、`avl_preorder_traverse()`或`avl_postorder_traverse()`函数遍历树。

示例代码如下：

```c
#include "avl_tree.h"

int main() {
    AvlTree tree = avl_init();
    
    // 插入元素
    avl_insert(tree, 10);
    avl_insert(tree, 20);
    avl_insert(tree, 30);

    // 查找元素
    if (avl_search(tree, 20)) {
        printf("Element found!\n");
    } else {
        printf("Element not found!\n");
    }

    // 删除元素
    avl_delete(tree, 20);

    // 遍历树
    avl_inorder_traverse(tree);

    return 0;
}
```

## 许可证
本项目采用MIT许可证，详情见[LICENSE](LICENSE)文件。