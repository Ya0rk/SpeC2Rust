import os
import subprocess
import shutil

# ================= 配置区域 =================
# 1. 你的编译命令 (确保它能尝试编译整个项目)
# 建议使用通配符或 Makefile，这样删除文件后命令依然有效
BUILD_COMMAND = "make all" 

# 2. 编译前的清理命令 (可选，防止增量编译干扰)
CLEAN_COMMAND = "make clean"

# 3. 绝对不能删除的文件 (如 main.c)
KEEP_FILES = ["cat.c"]

# 4. 需要尝试清理的目录
TARGET_DIR = "."
# ===========================================

def try_compile():
    """运行编译命令，返回是否成功"""
    if CLEAN_COMMAND:
        subprocess.run(CLEAN_COMMAND, shell=True, capture_output=True)
    
    # 运行编译
    result = subprocess.run(BUILD_COMMAND, shell=True, capture_output=False)
    return result.returncode == 0

def main():
    if not try_compile():
        print("错误：项目当前状态就无法编译通过，请检查 BUILD_COMMAND！")
        return

    # 获取所有 .c 和 .h 文件
    files = [f for f in os.listdir(TARGET_DIR) if f.endswith(('.c', '.h'))]
    files = [f for f in files if f not in KEEP_FILES]
    
    deleted_files = []
    
    print(f"开始扫描，共 {len(files)} 个待测文件...")

    for filename in files:
        print(f"正在测试文件: {filename} ... ", end="", flush=True)
        
        # 临时移动文件 (模拟删除)
        temp_name = filename + ".bak"
        shutil.move(filename, temp_name)
        
        if try_compile():
            # 编译依然成功，说明文件确实没用
            print("【多余】(已永久删除)")
            os.remove(temp_name) # 真正删除备份
            deleted_files.append(filename)
        else:
            # 编译失败，说明文件是必须的，恢复它
            print("【必要】(已还原)")
            shutil.move(temp_name, filename)

    print("\n" + "="*30)
    print(f"清理完成！总共删除了 {len(deleted_files)} 个文件:")
    for f in deleted_files:
        print(f" - {f}")
    print("="*30)

if __name__ == "__main__":
    # 运行前确认
    confirm = input("该脚本会尝试物理删除文件，建议先执行 git commit。确定开始？(y/n): ")
    if confirm.lower() == 'y':
        main()