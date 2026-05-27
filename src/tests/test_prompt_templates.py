import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.prompt import prompt_manager


class PromptTemplateTests(unittest.TestCase):
    def test_simplified_chinese_directive_removed_from_active_system_prompts(self):
        prompt_calls = [
            ("c_doc", "generate_spec_system_prompt"),
            ("c_doc", "generate_plan_system_prompt"),
            ("c_doc", "generate_tasks_system_prompt"),
            ("c_doc", "generate_module_spec_system_prompt"),
            ("c_doc", "generate_module_plan_system_prompt"),
            ("c_doc", "generate_module_tasks_system_prompt"),
            ("spec_agent", "generate_spec_system_prompt"),
            ("spec_agent", "generate_plan_system_prompt"),
            ("spec_agent", "generate_tasks_system_prompt"),
            ("spec_agent", "generate_module_spec_system_prompt"),
            ("spec_agent", "generate_module_plan_system_prompt"),
            ("spec_agent", "generate_module_tasks_system_prompt"),
        ]

        for agent_name, prompt_name in prompt_calls:
            prompt = prompt_manager.get(agent_name, prompt_name)
            self.assertNotIn(
                "Output must consistently use Simplified Chinese.",
                prompt,
                msg=f"{agent_name}.{prompt_name}",
            )
