import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.cmd import run

def specify_init(output_path: str, model_type="qwen", script="sh"):
    """
    进入输出目录，运行 specify init 命令
    
    参数:
        output_path: 输出目录路径
        model_type: 模型类型，如 "qwen" 等
        script: 脚本类型，如 "sh", "bash", 等
    """
    command = f"cd {output_path} && specify init . --force --ai {model_type} --script {script}  --ignore-agent-tools"
    run(command)