import subprocess

def run(command):
    """
        执行shell指令函数，出入参数为shell指令字符串，返回值为指令执行结果字符串
        如果指令执行失败，抛出RuntimeError异常
        
        输入:
            command: shell指令字符串
        
        返回值:
            指令执行结果字符串
            
        eg:
            run("ls -l")
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            timeout=25,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        # print(result)
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='ignore')
            return err
        return None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Timeout")