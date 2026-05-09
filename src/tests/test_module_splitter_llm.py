import importlib.util
import json
import sys
import unittest
from pathlib import Path

split_spec = importlib.util.spec_from_file_location(
    "module_splitter_under_test",
    Path(__file__).parent.parent / "agent" / "split.py",
)
split_module = importlib.util.module_from_spec(split_spec)
split_spec.loader.exec_module(split_module)
ModuleSplitter = split_module.ModuleSplitter


def make_func(name, file_path, start_line, source=None):
    source = source or f"int {name}(void) {{ return 0; }}"
    return {
        "func_defid": f"{file_path}:{name}",
        "span": f"{file_path}:{start_line}:1:{start_line + 2}:1",
        "num_lines": 3,
        "source": source,
        "calls": [],
    }


class SchemaAwareFakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def set_request_label(self, _label):
        pass

    def generate(self, messages):
        self.requests.append(messages)
        if self.replies:
            return [json.dumps(self.replies.pop(0))]
        return [json.dumps({"modules": [], "next_queries": []})]


class ModuleSplitterLlmBehaviorTests(unittest.TestCase):
    def test_duplicate_function_names_are_tracked_by_file_scoped_ids(self):
        project_info = {
            "project_name": "dupe",
            "c_files": ["example.c", "test.c"],
            "h_files": [],
            "entry_files": ["example.c", "test.c"],
        }
        project_analysis = {
            "functions": [
                make_func("main", "example.c", 1),
                make_func("main", "test.c", 1),
            ],
            "structs": [],
        }
        dependency_graph = {"call_graph": {}, "struct_usage": {}, "include_graph": {}}
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "example_entry",
                            "function_ids": ["example.c:main"],
                            "files": ["example.c", "root/example.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "test_entry",
                            "function_ids": ["test.c:main"],
                            "files": ["test.c"],
                            "confidence": "high",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=2).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        covered_ids = {
            func["id"]
            for module in modules
            for func in module.get("functions", [])
        }
        self.assertEqual(covered_ids, {"example.c:main", "test.c:main"})
        self.assertTrue(all("root/example.c" not in module.get("files", []) for module in modules))

    def test_private_helper_module_is_merged_into_small_calling_module(self):
        project_info = {
            "project_name": "merge",
            "c_files": ["core.c"],
            "h_files": [],
            "entry_files": ["core.c"],
        }
        project_analysis = {
            "functions": [
                make_func("public_check", "core.c", 1, "int public_check(void) { return private_check(); }"),
                make_func("private_check", "core.c", 8, "static int private_check(void) { return 1; }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {"public_check": ["private_check"]},
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "validation_api",
                            "function_ids": ["core.c:public_check"],
                            "files": ["core.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "validation_helper",
                            "function_ids": ["core.c:private_check"],
                            "files": ["core.c"],
                            "confidence": "high",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=2).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        merged = [
            module
            for module in modules
            if {"public_check", "private_check"}.issubset({func["name"] for func in module.get("functions", [])})
        ]
        self.assertEqual(len(merged), 1)
        self.assertIn("validation_helper", merged[0].get("merged_from", []))

    def test_cross_file_called_module_is_not_merged_into_caller(self):
        project_info = {
            "project_name": "boundary",
            "c_files": ["lib.c", "test.c"],
            "h_files": [],
            "entry_files": ["test.c"],
        }
        project_analysis = {
            "functions": [
                make_func("lib_create", "lib.c", 1, "void *lib_create(void) { return 0; }"),
                make_func("lib_destroy", "lib.c", 8, "void lib_destroy(void) { }"),
                make_func("run_test", "test.c", 1, "int run_test(void) { lib_create(); lib_destroy(); return 0; }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {"run_test": ["lib_create", "lib_destroy"]},
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "library_api",
                            "function_ids": ["lib.c:lib_create", "lib.c:lib_destroy"],
                            "files": ["lib.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "test_runner",
                            "function_ids": ["test.c:run_test"],
                            "files": ["test.c"],
                            "confidence": "high",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=2).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        lib_module = next(module for module in modules if module["name"] == "library_api")
        test_module = next(module for module in modules if module["name"] == "test_runner")
        self.assertEqual({func["name"] for func in lib_module["functions"]}, {"lib_create", "lib_destroy"})
        self.assertEqual({func["name"] for func in test_module["functions"]}, {"run_test"})

    def test_same_file_same_family_small_modules_are_merged(self):
        project_info = {
            "project_name": "same_family",
            "c_files": ["s.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("sdscat", "s.c", 1, "char *sdscat(void) { return sdscatlen(); }"),
                make_func("sdscatlen", "s.c", 8, "char *sdscatlen(void) { return 0; }"),
                make_func("sdscatprintf", "s.c", 16, "char *sdscatprintf(void) { return sdscatvprintf(); }"),
                make_func("sdscatvprintf", "s.c", 24, "char *sdscatvprintf(void) { return sdscatlen(); }"),
                make_func("sdstolower", "s.c", 32, "void sdstolower(void) { }"),
                make_func("sdstoupper", "s.c", 40, "void sdstoupper(void) { }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {
                "sdscat": ["sdscatlen"],
                "sdscatprintf": ["sdscatvprintf"],
                "sdscatvprintf": ["sdscatlen"],
            },
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "append_core",
                            "function_ids": ["s.c:sdscat", "s.c:sdscatlen"],
                            "files": ["s.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "formatted_append",
                            "function_ids": ["s.c:sdscatprintf", "s.c:sdscatvprintf"],
                            "files": ["s.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "case_transform",
                            "function_ids": ["s.c:sdstolower"],
                            "files": ["s.c"],
                            "confidence": "medium",
                        },
                        {
                            "name": "case_conversion",
                            "function_ids": ["s.c:sdstoupper"],
                            "files": ["s.c"],
                            "confidence": "medium",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        function_sets = [{func["name"] for func in module["functions"]} for module in modules]
        self.assertTrue(any({"sdscat", "sdscatlen", "sdscatprintf", "sdscatvprintf"}.issubset(names) for names in function_sets))
        self.assertTrue(any({"sdstolower", "sdstoupper"}.issubset(names) for names in function_sets))

    def test_entry_module_does_not_absorb_called_same_file_modules(self):
        project_info = {
            "project_name": "entry_protect",
            "c_files": ["app.c"],
            "h_files": [],
            "entry_files": ["app.c"],
        }
        project_analysis = {
            "functions": [
                make_func("main", "app.c", 1, "int main(void) { run_tests(); return 0; }"),
                make_func("run_tests", "app.c", 10, "void run_tests(void) { helper_case(); }"),
                make_func("helper_case", "app.c", 20, "void helper_case(void) { }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {"main": ["run_tests"], "run_tests": ["helper_case"]},
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "test_entry",
                            "function_ids": ["app.c:main"],
                            "files": ["app.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "test_cases",
                            "function_ids": ["app.c:run_tests", "app.c:helper_case"],
                            "files": ["app.c"],
                            "confidence": "high",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        entry = next(module for module in modules if module["name"] == "test_entry")
        self.assertEqual({func["name"] for func in entry["functions"]}, {"main"})

    def test_public_prefix_only_modules_are_not_merged_without_semantic_evidence(self):
        project_info = {
            "project_name": "public_prefix",
            "c_files": ["sds.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("sdsfree", "sds.c", 1, "void sdsfree(void) { }"),
                make_func("sdsupdatelen", "sds.c", 8, "void sdsupdatelen(void) { }"),
                make_func("sdsclear", "sds.c", 16, "void sdsclear(void) { }"),
                make_func("sdsfreesplitres", "sds.c", 24, "void sdsfreesplitres(void) { }"),
            ],
            "structs": [],
        }
        dependency_graph = {"call_graph": {}, "struct_usage": {}, "include_graph": {}}
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "sds_memory_lifecycle",
                            "function_ids": ["sds.c:sdsfree", "sds.c:sdsupdatelen", "sds.c:sdsclear"],
                            "files": ["sds.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "split_result_memory_management",
                            "function_ids": ["sds.c:sdsfreesplitres"],
                            "files": ["sds.c"],
                            "confidence": "high",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        function_sets = [{func["name"] for func in module["functions"]} for module in modules]
        self.assertIn({"sdsfree", "sdsupdatelen", "sdsclear"}, function_sets)
        self.assertIn({"sdsfreesplitres"}, function_sets)

    def test_mixed_public_helper_module_is_purified_before_merge(self):
        project_info = {
            "project_name": "mixed_helper",
            "c_files": ["sds.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("sdssplitargs", "sds.c", 1, "int sdssplitargs(void) { return hex_digit_to_int(); }"),
                make_func("sdssplitlen", "sds.c", 8, "int sdssplitlen(void) { return 0; }"),
                make_func("hex_digit_to_int", "sds.c", 16, "static int hex_digit_to_int(void) { return 0; }"),
                make_func("sdsmapchars", "sds.c", 24, "int sdsmapchars(void) { return 0; }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {"sdssplitargs": ["hex_digit_to_int"]},
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "sds_argument_parsing",
                            "function_ids": ["sds.c:sdssplitargs", "sds.c:sdssplitlen"],
                            "files": ["sds.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "argument_parsing_helpers",
                            "function_ids": ["sds.c:hex_digit_to_int", "sds.c:sdsmapchars"],
                            "files": ["sds.c"],
                            "confidence": "medium",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        function_sets = [{func["name"] for func in module["functions"]} for module in modules]
        self.assertIn({"sdssplitargs", "sdssplitlen", "hex_digit_to_int"}, function_sets)
        self.assertIn({"sdsmapchars"}, function_sets)

    def test_llm_mixed_public_module_is_split_into_coherent_components(self):
        project_info = {
            "project_name": "purify_public",
            "c_files": ["sds.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("sdsHdrSize", "sds.c", 1, "int sdsHdrSize(void) { return 0; }"),
                make_func("sdsReqType", "sds.c", 8, "int sdsReqType(void) { return 0; }"),
                make_func("sdsAllocSize", "sds.c", 16, "int sdsAllocSize(void) { return sdsHdrSize(); }"),
                make_func("sdsAllocPtr", "sds.c", 24, "void *sdsAllocPtr(void) { return 0; }"),
                make_func("sdsRemoveFreeSpace", "sds.c", 32, "int sdsRemoveFreeSpace(void) { return sdsHdrSize() + sdsReqType(); }"),
                make_func("sdsfromlonglong", "sds.c", 40, "int sdsfromlonglong(void) { return 0; }"),
                make_func("sdssplitlen", "sds.c", 48, "int sdssplitlen(void) { return 0; }"),
                make_func("sdsfreesplitres", "sds.c", 56, "void sdsfreesplitres(void) { }"),
                make_func("sdstoupper", "sds.c", 64, "void sdstoupper(void) { }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {
                "sdsAllocSize": ["sdsHdrSize"],
                "sdsRemoveFreeSpace": ["sdsHdrSize", "sdsReqType"],
            },
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "header_layout_and_allocation_metadata",
                            "description": "Compute SDS header type and allocation metadata from the string header layout.",
                            "function_ids": [
                                "sds.c:sdsHdrSize",
                                "sds.c:sdsReqType",
                                "sds.c:sdsAllocSize",
                                "sds.c:sdsAllocPtr",
                                "sds.c:sdsRemoveFreeSpace",
                                "sds.c:sdsfromlonglong",
                                "sds.c:sdssplitlen",
                                "sds.c:sdsfreesplitres",
                                "sds.c:sdstoupper",
                            ],
                            "files": ["sds.c"],
                            "confidence": "high",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        header_module = next(
            module
            for module in modules
            if {"sdsHdrSize", "sdsAllocSize"}.issubset({func["name"] for func in module["functions"]})
        )
        header_names = {func["name"] for func in header_module["functions"]}
        self.assertTrue({"sdsHdrSize", "sdsAllocSize", "sdsAllocPtr"}.issubset(header_names))
        self.assertNotIn("sdsfreesplitres", header_names)
        self.assertNotIn("sdstoupper", header_names)
        self.assertTrue(any({func["name"] for func in module["functions"]} == {"sdsfreesplitres"} for module in modules))
        self.assertTrue(any({func["name"] for func in module["functions"]} == {"sdstoupper"} for module in modules))

    def test_small_project_modules_are_coarsened_for_planning(self):
        project_info = {
            "project_name": "small_planning",
            "c_files": ["avl_test.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("tree_create", "avl_test.c", 1, "void tree_create(void) { }"),
                make_func("tree_find", "avl_test.c", 8, "void tree_find(void) { }"),
                make_func("tree_print", "avl_test.c", 16, "void tree_print(void) { }"),
                make_func("tree_insert", "avl_test.c", 24, "void tree_insert(void) { }"),
                make_func("tree_delete", "avl_test.c", 32, "void tree_delete(void) { }"),
                make_func("tree_check", "avl_test.c", 40, "void tree_check(void) { }"),
                make_func("unit_test_create", "avl_test.c", 48, "void unit_test_create(void) { tree_create(); tree_check(); }"),
                make_func("unit_test_find", "avl_test.c", 56, "void unit_test_find(void) { tree_find(); tree_check(); }"),
                make_func("unit_test_min", "avl_test.c", 64, "void unit_test_min(void) { tree_create(); tree_check(); }"),
                make_func("unit_test_dup", "avl_test.c", 72, "void unit_test_dup(void) { tree_insert(); tree_check(); }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {
                "unit_test_create": ["tree_create", "tree_check"],
                "unit_test_find": ["tree_find", "tree_check"],
                "unit_test_min": ["tree_create", "tree_check"],
                "unit_test_dup": ["tree_insert", "tree_check"],
            },
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "test_tree_helpers",
                            "function_ids": ["avl_test.c:tree_create", "avl_test.c:tree_find", "avl_test.c:tree_print"],
                            "files": ["avl_test.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "test_tree_mutation_helpers",
                            "function_ids": ["avl_test.c:tree_insert", "avl_test.c:tree_delete", "avl_test.c:tree_check"],
                            "files": ["avl_test.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "test_basic_operations",
                            "function_ids": ["avl_test.c:unit_test_create", "avl_test.c:unit_test_find"],
                            "files": ["avl_test.c"],
                            "confidence": "high",
                        },
                        {
                            "name": "test_special_cases",
                            "function_ids": ["avl_test.c:unit_test_min", "avl_test.c:unit_test_dup"],
                            "files": ["avl_test.c"],
                            "confidence": "high",
                        },
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        self.assertLessEqual(len(modules), 3)
        covered = {
            func["name"]
            for module in modules
            for func in module["functions"]
        }
        self.assertEqual(
            covered,
            {
                "tree_create",
                "tree_find",
                "tree_print",
                "tree_insert",
                "tree_delete",
                "tree_check",
                "unit_test_create",
                "unit_test_find",
                "unit_test_min",
                "unit_test_dup",
            },
        )

    def test_responsibility_labels_use_tokens_not_substrings(self):
        project_info = {
            "project_name": "cleanup",
            "c_files": ["cleanup.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("destroy", "cleanup.c", 1, "void destroy(void) { }"),
            ],
            "structs": [],
        }
        dependency_graph = {"call_graph": {}, "struct_usage": {}, "include_graph": {}}

        modules, _ = ModuleSplitter(use_llm=False).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        labels = modules[0]["static_summary"]["responsibilities"]
        self.assertNotIn("string_processing", labels)

    def test_static_fallback_is_split_by_file_and_prefix(self):
        project_info = {
            "project_name": "fallback",
            "c_files": ["lib.c", "data.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("lib_rotate_left", "lib.c", 1, "static void lib_rotate_left(void) { }"),
                make_func("lib_rotate_right", "lib.c", 8, "static void lib_rotate_right(void) { }"),
                make_func("data_create", "data.c", 1, "void *data_create(void) { return 0; }"),
                make_func("data_destroy", "data.c", 8, "void data_destroy(void) { }"),
            ],
            "structs": [],
        }
        dependency_graph = {"call_graph": {}, "struct_usage": {}, "include_graph": {}}
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "one_llm_module",
                            "function_ids": ["lib.c:lib_rotate_left"],
                            "files": ["lib.c"],
                            "confidence": "medium",
                        }
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        fallback_modules = [module for module in modules if module.get("coverage_method") == "static_fallback"]
        self.assertGreaterEqual(len(fallback_modules), 2)
        self.assertTrue(any(module["files"] == ["lib.c"] for module in fallback_modules))
        self.assertTrue(any(module["files"] == ["data.c"] for module in fallback_modules))

    def test_static_fallback_prefers_same_file_call_components(self):
        project_info = {
            "project_name": "fallback_graph",
            "c_files": ["core.c"],
            "h_files": [],
            "entry_files": [],
        }
        project_analysis = {
            "functions": [
                make_func("api_entry", "core.c", 1, "void api_entry(void) { }"),
                make_func("rotate_left", "core.c", 10, "static void rotate_left(void) { }"),
                make_func("rotate_right", "core.c", 20, "static void rotate_right(void) { }"),
                make_func("fix_left", "core.c", 30, "static void fix_left(void) { rotate_left(); }"),
                make_func("fix_right", "core.c", 40, "static void fix_right(void) { rotate_right(); }"),
                make_func("debug_print", "core.c", 50, "static void debug_print(void) { }"),
                make_func("destroy_nodes", "core.c", 60, "static void destroy_nodes(void) { }"),
            ],
            "structs": [],
        }
        dependency_graph = {
            "call_graph": {
                "fix_left": ["rotate_left"],
                "fix_right": ["rotate_right"],
            },
            "struct_usage": {},
            "include_graph": {},
        }
        llm = SchemaAwareFakeLLM(
            [
                {
                    "modules": [
                        {
                            "name": "api",
                            "function_ids": ["core.c:api_entry"],
                            "files": ["core.c"],
                            "confidence": "medium",
                        }
                    ],
                    "next_queries": [],
                }
            ]
        )

        modules, _ = ModuleSplitter(llm=llm, llm_max_rounds=1).split(
            project_info,
            project_analysis,
            dependency_graph,
        )

        fallback_modules = [module for module in modules if module.get("coverage_method") == "static_fallback"]
        fallback_sets = [{func["name"] for func in module["functions"]} for module in fallback_modules]
        self.assertTrue(any({"fix_left", "rotate_left"}.issubset(names) for names in fallback_sets))
        self.assertTrue(any({"fix_right", "rotate_right"}.issubset(names) for names in fallback_sets))
        self.assertFalse(any({"rotate_left", "rotate_right", "debug_print", "destroy_nodes"}.issubset(names) for names in fallback_sets))


if __name__ == "__main__":
    unittest.main()
