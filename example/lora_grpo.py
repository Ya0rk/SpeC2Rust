import torch
from datasets import load_dataset
from transformers import AutoTokenizer, AutoModelForCausalLM
from trl import GRPOConfig, GRPOTrainer
from peft import LoraConfig, get_peft_model, TaskType, PeftModel
import os
import subprocess
import tempfile
import json
import requests
import shutil
import re
from typing import List, Dict, Any
from dataset_split import conversation_prompt_translation
import logging
from repo_complie_reward import compile_reward_function, remove_comments
from codebleu import calc_codebleu

import torch.distributed as dist
if not dist.is_initialized():
    dist.init_process_group(backend="nccl")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# os.environ["CUDA_VISIBLE_DEVICES"] = "1" 
os.environ["TOKENIZERS_PARALLELISM"] = "True"

# 加载数据集
dataset = load_dataset("json", data_files={
    "train": "/data1/jfeng/c2rust/train/dataset/grpo_train.jsonl",
})

# 模型路径
model_name = "/data/jfeng/models/Qwen2.5-Coder-7B-Instruct"
sft_model_path = "/data1/jfeng/c2rust/train/train_result/train_lora"

# 创建运行时目录
RUNTIME_DIR = "./runtime"
os.makedirs(RUNTIME_DIR, exist_ok=True)

a = 1
b = 0

# 新增：checkpoint 相关配置
RESUME_FROM_CHECKPOINT = True  # 设置为 True 来启用从 checkpoint 继续训练
CHECKPOINT_PATH = f"/data1/jfeng/c2rust/train/grpo_result_{a}_{b}"  # checkpoint 目录路径

def find_latest_checkpoint(checkpoint_dir):
    """查找最新的 checkpoint"""
    if not os.path.exists(checkpoint_dir):
        return None
    
    # 查找所有 checkpoint 文件夹
    checkpoint_folders = []
    for item in os.listdir(checkpoint_dir):
        if item.startswith("checkpoint-"):
            try:
                step_num = int(item.split("-")[1])
                checkpoint_folders.append((step_num, os.path.join(checkpoint_dir, item)))
            except ValueError:
                continue
    
    if not checkpoint_folders:
        return None
    
    # 返回最新的 checkpoint
    checkpoint_folders.sort(key=lambda x: x[0], reverse=True)
    latest_checkpoint = checkpoint_folders[0][1]
    logger.info(f"找到最新的 checkpoint: {latest_checkpoint}")
    return latest_checkpoint

class FuncCodeEvaluator:
    """代码评估器"""
    def __init__(self, server_url: str = "http://10.249.44.170:5000"):
        self.server_url = server_url
        self.execute_code_url = f"{server_url}/api/execute_code"
        self._session = requests.Session()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._session.close()
    
    def execute_code(
        self,
        source_code: str,
        unittests: List[dict],
        compiler: str = "Rust 2018",
        limits: dict = None,
        block_network: bool = True,
        stop_on_first_fail: bool = True,
        use_sanitizer: bool = False,
    ):
        request_body = dict(
            language=compiler,
            source_code=source_code,
            unittests=unittests,
            limits=limits if isinstance(limits, dict) else None,
            compile_cmd=None,
            compile_flags=None,
            execute_cmd=None,
            execute_flags=None,
            block_network=block_network,
            stop_on_first_fail=stop_on_first_fail,
            use_sanitizer=use_sanitizer,
        )
        
        try:
            json_response = self._session.post(
                self.execute_code_url,
                json=request_body,
                headers={"Content-Type": "application/json"},
            ).json()

            if "data" not in json_response:
                return json_response

            return json_response["data"]
        except Exception as e:
            return {"error": str(e)}
    
    def analyze_runtime_result(self, runtime_result):
        """分析运行时结果，返回通过的测试数量和总测试数量"""
        if isinstance(runtime_result, dict) and "error" in runtime_result:
            return 0, 1
        
        passed_tests = 0
        total_tests = len(runtime_result) if isinstance(runtime_result, list) else 0
        
        if total_tests == 0:
            return 0, 1
        
        for result in runtime_result:
            if result.get('exec_outcome') == 'PASSED':
                passed_tests += 1
        return passed_tests, total_tests

# 全局代码评估器
func_code_evaluator = FuncCodeEvaluator()

def check_func_compilation(rust_code: str) -> float:
    """检查Rust代码是否能够编译成功，返回编译奖励"""
    try:
        temp_file = os.path.join(RUNTIME_DIR, f"temp_{os.getpid()}_{hash(rust_code) % 10000}.rs")
        temp_file = os.path.abspath(temp_file)
        
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(rust_code)
            
        result = subprocess.run(
            ['rustc', '--crate-type', 'lib', temp_file, '--error-format', 'json'],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=RUNTIME_DIR
        )
        
        if os.path.exists(temp_file):
            os.unlink(temp_file)
        error_count = 0
        if result.returncode != 0:
            error_lines = result.stderr.strip().split('\n')
            for line in error_lines:
                try:
                    error_data = json.loads(line)
                    if error_data.get('level') == 'error' and 'previous error' not in error_data.get('message', '').lower():
                        error_count += 1
                except (json.JSONDecodeError, KeyError):
                    if 'error' in line.lower():
                        error_count += 1
        
        reward = 1.0 / (1.0 + error_count)
        return reward
                
    except subprocess.TimeoutExpired:
        return 0.1
    except Exception as e:
        return 0.0

def run_remote_func_tests(rust_code: str, unittests: List[dict]) -> float:
    """通过远程执行环境运行测试，返回测试奖励"""
    try:
        runtime_result = func_code_evaluator.execute_code(rust_code, unittests)
        passed_tests, total_tests = func_code_evaluator.analyze_runtime_result(runtime_result)
        
        if total_tests == 0:
            return 0.0
        
        reward = passed_tests / total_tests
        return reward
            
    except Exception as e:
        logger.error(f"远程测试执行错误: {e}")
        return 0.0

def post_process_rust_code(generated_text: str) -> str:
    """从生成的文本中提取Rust代码"""
    # 移除系统提示和用户提示部分
    if "```rust" in generated_text:
        match = re.search(r"```rust\n(.*?)```", generated_text, re.DOTALL)
        if match:
            return match.group(1).strip()
    
    # 通用代码块匹配
    match = re.search(r"```(?:\w+)?\n(.*?)```", generated_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # 如果没有代码块，返回原始文本
    return generated_text.strip()

def func_reward(rust_code, unittest):
    total_reward = 0.0
    
    compile_reward = check_func_compilation(rust_code)
    # test_reward = run_remote_func_tests(rust_code, unittest)
    test_reward = 0.0
    total_reward += a * compile_reward + b * test_reward
    
    return total_reward

def repo_reward(rust_code, metdata):
    total_reward = 0.0
    rust_path = metdata.get('rust_path', '')
    ref_rust_code = metdata.get('rust_code', '')
    
    compile_reward = compile_reward_function(rust_path, rust_code, ref_rust_code)
    # codebleu_score = calc_codebleu([remove_comments(ref_rust_code)], [remove_comments(rust_code)], lang='rust')
    # codebleu_reward = codebleu_score['codebleu']
    codebleu_reward = 0.0
    
    total_reward += a * compile_reward + b * codebleu_reward
    
    return total_reward
    

def compile_and_test_reward(completions: List[str], **kwargs) -> List[float]:
    """
    GRPO奖励函数：计算基于编译器反馈和测试用例的奖励
    
    Args:
        completions: 生成的完成文本列表
        **kwargs: 额外的上下文信息
        
    Returns:
        List[float]: 每个完成文本对应的奖励值
    """
    rewards = []
    
    # 从kwargs中获取unittests信息（如果有的话）
    unittests = kwargs.get('unittests', [])
    metadata = kwargs.get('metadata', {})
    
    for completion, unittest, metdadata_item in zip(completions, unittests, metadata):
        try:
            # 提取Rust代码
            rust_code = post_process_rust_code(completion[0]['content'])
            
            if not rust_code.strip():
                rewards.append(0.0)
                continue
            if unittest:
                total_reward = func_reward(rust_code, unittest)
            else:
                total_reward = repo_reward(rust_code, metdadata_item)
        
            rewards.append(total_reward)
            
        except Exception as e:
            import traceback
            logger.error(f"处理生成文本时出错: {traceback.format_exc()}")
            logger.error(f"计算奖励时出错: {e}")
            rewards.append(0.0)
    print(f"计算的奖励: {rewards}")
    mean_reward = sum(rewards) / len(rewards) if rewards else 0.0
    rewards = [reward - mean_reward for reward in rewards]
    print(f"baseline 减法后的奖励: {rewards}")
    return rewards

def preprocess_dataset(dataset):
    """预处理数据集，转换为GRPO训练所需的格式"""
    def format_example(example):
        c_code = example.get('source_code', '')
        unittests = example.get('hidden_unit_tests', [])
        rust_path = example.get('rust_path', '')
        rust_code = example.get('rust_code', '')
        
        if c_code and unittests:
            prompt = [
                {'role': 'system', 'content': conversation_prompt_translation['system']},
                {'role': 'user', 'content': conversation_prompt_translation['user'].format(source_code=c_code)}
            ]
        else:
            prompt = example.get('prompt', [])
            print(prompt)
        return {
            'prompt': prompt,
            'unittests': unittests,
            'metadata': {
                'rust_path': rust_path,
                'rust_code': rust_code,
            }
        }
            
    return dataset.map(format_example)

def setup_model_and_tokenizer():
    """设置模型和分词器"""
    # 加载分词器
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    
    # 加载基础模型
    base_model = AutoModelForCausalLM.from_pretrained(
        '/data1/jfeng/c2rust/train/qwc-7b-lora-mft',
        trust_remote_code=True,
        # device_map="auto",
        torch_dtype=torch.bfloat16
    )
    
    # # 加载SFT模型
    # model = PeftModel.from_pretrained(base_model, sft_model_path)
    # model = model.merge_and_unload()
    
    return base_model, tokenizer

def train_grpo():
    """GRPO训练主函数"""
    try:
        # 设置模型和分词器
        model, tokenizer = setup_model_and_tokenizer()
        
        # 预处理数据集
        processed_dataset = preprocess_dataset(dataset["train"])
        
        # 检查是否需要从 checkpoint 继续训练
        resume_from_checkpoint = None
        if RESUME_FROM_CHECKPOINT:
            resume_from_checkpoint = find_latest_checkpoint(CHECKPOINT_PATH)
            if resume_from_checkpoint:
                logger.info(f"将从 checkpoint 继续训练: {resume_from_checkpoint}")
            else:
                logger.info("未找到 checkpoint，将从头开始训练")
        
        # GRPO配置
        training_args = GRPOConfig(
            output_dir=CHECKPOINT_PATH,
            learning_rate=5e-5,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            num_train_epochs=3,
            logging_steps=50,
            save_steps=500,
            do_train=True,
            warmup_steps=100,
            remove_unused_columns=False,
            dataloader_drop_last=True,
            seed=42,
            bf16=True,
            gradient_checkpointing=True,
            max_prompt_length=6500,
            max_completion_length=1024,
            use_vllm=True,
            vllm_mode="colocate",
            vllm_gpu_memory_utilization=0.8,
            vllm_tensor_parallel_size=1,
            ddp_find_unused_parameters=False,
            # deepspeed='/data1/jfeng/c2rust/train/deepspeed_zero3.yaml',
        )
        
        # LoRA配置
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=16,
            lora_alpha=32,
            lora_dropout=0.1,
            target_modules=["q_proj", "v_proj", "k_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            bias="none",
        )
        
        # 创建GRPO训练器
        trainer = GRPOTrainer(
            model=model,
            args=training_args,
            train_dataset=processed_dataset,
            reward_funcs=compile_and_test_reward,
            peft_config=lora_config,
        )
        
        # 开始训练（传入 resume_from_checkpoint 参数）
        logger.info("开始GRPO训练...")
        trainer.train(resume_from_checkpoint=resume_from_checkpoint)
        
        logger.info("GRPO训练完成")
        
    except Exception as e:
        logger.error(f"训练过程中出错: {e}")
        raise e
    
    finally:
        # 清理资源
        if os.path.exists(RUNTIME_DIR):
            shutil.rmtree(RUNTIME_DIR)
        func_code_evaluator.__exit__(None, None, None)

if __name__ == "__main__":
    train_grpo()