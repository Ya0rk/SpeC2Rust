import sys
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent import main as main_module


class MainCDocsFlagTests(unittest.TestCase):
    def make_args(self, **overrides):
        defaults = {
            "skip_c_analysis": False,
            "use_spec_agent": False,
            "use_spec_json_agent": False,
            "use_pointer_agent": False,
            "use_macro_agent": False,
            "freeze_c_docs": False,
            "use_rust_repair_agent": False,
            "skip_code_fix": False,
            "skip_test_fix": False,
        }
        defaults.update(overrides)
        return Namespace(**defaults)

    def test_freeze_c_docs_disables_all_c_doc_writers(self):
        args = self.make_args(
            use_spec_agent=True,
            use_spec_json_agent=True,
            use_pointer_agent=True,
            use_macro_agent=True,
            freeze_c_docs=True,
        )

        self.assertFalse(main_module.should_run_primary_c_analysis(args))
        self.assertFalse(main_module.should_run_spec_json_stage(args))
        self.assertFalse(main_module.should_run_pointer_stage(args))
        self.assertFalse(main_module.should_run_macro_stage(args))

    def test_skip_c_analysis_only_disables_primary_analysis(self):
        args = self.make_args(
            skip_c_analysis=True,
            use_pointer_agent=True,
            use_macro_agent=True,
        )

        self.assertFalse(main_module.should_run_primary_c_analysis(args))
        self.assertTrue(main_module.should_run_pointer_stage(args))
        self.assertTrue(main_module.should_run_macro_stage(args))

    def test_spec_json_requires_spec_agent_and_writable_c_docs(self):
        args = self.make_args(use_spec_json_agent=True)
        self.assertFalse(main_module.should_run_spec_json_stage(args))

        args = self.make_args(
            use_spec_agent=True,
            use_spec_json_agent=True,
            freeze_c_docs=True,
        )
        self.assertFalse(main_module.should_run_spec_json_stage(args))

    def test_rust_repair_stage_is_controlled_by_optional_flag(self):
        self.assertFalse(main_module.should_run_rust_repair_stage(self.make_args()))
        self.assertTrue(
            main_module.should_run_rust_repair_stage(
                self.make_args(use_rust_repair_agent=True)
            )
        )

    def test_rust_repair_agent_replaces_legacy_fixers(self):
        args = self.make_args(use_rust_repair_agent=True)

        self.assertTrue(main_module.should_run_rust_repair_stage(args))
        self.assertFalse(main_module.should_run_legacy_code_fix_stage(args))
        self.assertFalse(main_module.should_run_legacy_test_fix_stage(args))

    def test_legacy_fixers_run_only_when_not_replaced_and_not_skipped(self):
        self.assertTrue(main_module.should_run_legacy_code_fix_stage(self.make_args()))
        self.assertTrue(main_module.should_run_legacy_test_fix_stage(self.make_args()))
        self.assertFalse(main_module.should_run_legacy_code_fix_stage(self.make_args(skip_code_fix=True)))
        self.assertFalse(main_module.should_run_legacy_test_fix_stage(self.make_args(skip_test_fix=True)))


if __name__ == "__main__":
    unittest.main()
