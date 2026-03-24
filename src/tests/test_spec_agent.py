import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.spec_agent import SpecAgent
from config.config import Config


project_path_prefix = Path(__file__).parent.parent.parent

def test_spec_agent():
    config = Config(config_path=None, model_name="qwen32")
    spec_agent = SpecAgent(config=config)
    project_path = project_path_prefix / "datasets" / "avl-tree"
    output_dir = project_path_prefix / "output" / "avl-tree_spec02"
    spec_agent.analyze_and_generate_spec(project_path, output_dir)

if __name__ == "__main__":
    test_spec_agent()