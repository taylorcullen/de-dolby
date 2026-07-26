import json
import copy
import re
import shutil
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from harness.cli import (
    HarnessError,
    load_backlog,
    load_config,
    ralph_complete,
    ralph_start,
    ralph_verify,
    recover_lifecycle_transaction,
    ralph_status,
    repository_root,
    run_checks,
    show_ralph_prompt,
    validate_backlog,
    validate_prd,
)


class HarnessTests(unittest.TestCase):
    def copy_harness_repository(self, directory):
        source = repository_root()
        root = Path(directory)
        shutil.copytree(source / "ai", root / "ai")
        shutil.rmtree(root / "ai/ralph", ignore_errors=True)
        for relative_path in ("AGENTS.md", "README.md", "pyproject.toml"):
            shutil.copy2(source / relative_path, root / relative_path)
        backlog_path = root / "ai/backlog.json"
        backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
        next(
            item for item in backlog["items"] if item["id"] == "DD-010"
        )["status"] = "ready"
        backlog_path.write_text(json.dumps(backlog, indent=2) + "\n", encoding="utf-8")
        prd_path = root / "ai/prds/DD-010-ralph-lifecycle.md"
        prd_text = prd_path.read_text(encoding="utf-8")
        prd_path.write_text(
            re.sub(
                r"^Status:.*$",
                "Status: Ready  ",
                prd_text,
                count=1,
                flags=re.MULTILINE,
            ),
            encoding="utf-8",
        )
        return root, load_config(root)

    def test_repository_root_is_discovered_from_child(self):
        root = repository_root(Path("harness"))
        self.assertTrue((root / "ai" / "harness.json").is_file())

    def test_checked_in_configuration_is_valid(self):
        root = repository_root()
        config = load_config(root)
        self.assertEqual(config["version"], 1)
        self.assertIn("product", config["scopes"])
        self.assertIn("harness", config["scopes"])
        backlog = load_backlog(root, config)
        self.assertEqual(len(backlog["items"]), 10)
        self.assertEqual(len({item["id"] for item in backlog["items"]}), 10)

    def test_missing_context_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ai").mkdir()
            (root / "ai" / "harness.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "context": ["missing.md"],
                        "scopes": {
                            "test": [
                                {"name": "test", "command": ["python", "--version"]}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(HarnessError, "context file"):
                load_config(root)

    @patch("harness.cli.subprocess.run")
    def test_failed_gate_stops_check(self, run):
        run.return_value.returncode = 7
        config = {
            "scopes": {
                "harness": [
                    {"name": "first", "command": ["first"]},
                    {"name": "second", "command": ["second"]},
                ]
            }
        }
        self.assertEqual(run_checks(Path.cwd(), config, "harness"), 7)
        run.assert_called_once()

    @patch("harness.cli.subprocess.run")
    def test_python_placeholder_uses_active_interpreter(self, run):
        run.return_value.returncode = 0
        config = {
            "scopes": {
                "harness": [
                    {"name": "test", "command": ["{python}", "-m", "unittest"]}
                ]
            }
        }
        self.assertEqual(run_checks(Path.cwd(), config, "harness"), 0)
        self.assertEqual(run.call_args.args[0][0], sys.executable)

    def test_ralph_prompt_selects_first_ready_prd(self):
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        next(
            item for item in backlog["items"] if item["id"] == "DD-010"
        )["status"] = "ready"
        with patch("builtins.print") as output:
            self.assertEqual(show_ralph_prompt(root, backlog, None), 0)
        self.assertIn("for DD-010:", output.call_args.args[0])

    def test_ralph_status_json_reports_executable_items(self):
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        backlog = copy.deepcopy(backlog)
        next(
            item for item in backlog["items"] if item["id"] == "DD-010"
        )["status"] = "ready"
        with patch("builtins.print") as output:
            self.assertEqual(ralph_status(backlog, as_json=True), 0)
        document = json.loads(output.call_args.args[0])
        self.assertEqual(document["schema_version"], 1)
        records = {item["id"]: item for item in document["items"]}
        self.assertFalse(records["DD-009"]["executable"])
        self.assertTrue(records["DD-010"]["executable"])
        self.assertEqual(records["DD-010"]["unmet_dependencies"], [])

    def test_ralph_status_human_output_is_read_only(self):
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        before = copy.deepcopy(backlog)
        with patch("builtins.print") as output:
            self.assertEqual(ralph_status(backlog), 0)
        self.assertEqual(backlog, before)
        self.assertIn("DD-010", output.call_args_list[-1].args[0])

    def test_ralph_start_dry_run_does_not_modify_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self.copy_harness_repository(directory)
            backlog = load_backlog(root, config)
            backlog_before = (root / config["backlog"]).read_bytes()
            prd_path = root / "ai/prds/DD-010-ralph-lifecycle.md"
            prd_before = prd_path.read_bytes()
            self.assertEqual(
                ralph_start(root, config, backlog, "DD-010", dry_run=True), 0
            )
            self.assertEqual((root / config["backlog"]).read_bytes(), backlog_before)
            self.assertEqual(prd_path.read_bytes(), prd_before)

    def test_ralph_start_enforces_single_active_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self.copy_harness_repository(directory)
            backlog = load_backlog(root, config)
            self.assertEqual(ralph_start(root, config, backlog, "DD-010"), 0)
            updated = load_backlog(root, config)
            self.assertEqual(
                next(
                    item for item in updated["items"] if item["id"] == "DD-010"
                )["status"],
                "in_progress",
            )
            with self.assertRaisesRegex(HarnessError, "already in progress"):
                ralph_start(root, config, updated, None)

    def test_ralph_complete_requires_checked_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self.copy_harness_repository(directory)
            backlog = load_backlog(root, config)
            ralph_start(root, config, backlog, "DD-010")
            prd_path = root / "ai/prds/DD-010-ralph-lifecycle.md"
            text = prd_path.read_text(encoding="utf-8")
            prd_path.write_text(
                text.replace("- [x]", "- [ ]", 1), encoding="utf-8"
            )
            with self.assertRaisesRegex(HarnessError, "unchecked work"):
                ralph_complete(root, config, load_backlog(root, config), "DD-010")

    def test_ralph_complete_requires_current_passing_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self.copy_harness_repository(directory)
            backlog = load_backlog(root, config)
            ralph_start(root, config, backlog, "DD-010")
            prd_path = root / "ai/prds/DD-010-ralph-lifecycle.md"
            text = prd_path.read_text(encoding="utf-8")
            prd_path.write_text(
                re.sub(r"^- \[ \]", "- [x]", text, flags=re.MULTILINE),
                encoding="utf-8",
            )
            active = load_backlog(root, config)
            with self.assertRaisesRegex(HarnessError, "verification receipt"):
                ralph_complete(root, config, active, "DD-010")
            with patch("harness.cli.run_checks", return_value=0):
                self.assertEqual(
                    ralph_verify(root, config, active, "DD-010", "harness"), 0
                )
            self.assertEqual(ralph_complete(root, config, active, "DD-010"), 0)
            completed = load_backlog(root, config)
            self.assertEqual(
                next(
                    item for item in completed["items"] if item["id"] == "DD-010"
                )["status"],
                "done",
            )

    def test_interrupted_lifecycle_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root, config = self.copy_harness_repository(directory)
            backlog_path = root / config["backlog"]
            prd_path = root / "ai/prds/DD-010-ralph-lifecycle.md"
            backlog_before = backlog_path.read_text(encoding="utf-8")
            prd_before = prd_path.read_text(encoding="utf-8")
            prd_after = prd_before.replace("Status: Ready", "Status: In Progress")
            journal = {
                "schema_version": 1,
                "changes": [
                    {
                        "path": prd_path.relative_to(root).as_posix(),
                        "before": prd_before,
                        "after": prd_after,
                    },
                    {
                        "path": backlog_path.relative_to(root).as_posix(),
                        "before": backlog_before,
                        "after": backlog_before.replace(
                            '"status": "ready"', '"status": "in_progress"', 1
                        ),
                    },
                ],
            }
            transaction_path = root / "ai/.ralph-transaction.json"
            transaction_path.write_text(json.dumps(journal), encoding="utf-8")
            prd_path.write_text(prd_after, encoding="utf-8")
            recover_lifecycle_transaction(root)
            self.assertEqual(prd_path.read_text(encoding="utf-8"), prd_before)
            self.assertEqual(backlog_path.read_text(encoding="utf-8"), backlog_before)
            self.assertFalse(transaction_path.exists())

    def test_invalid_lifecycle_transaction_is_preserved_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.copy_harness_repository(directory)
            transaction_path = root / "ai/.ralph-transaction.json"
            transaction_path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(HarnessError, "invalid lifecycle transaction"):
                recover_lifecycle_transaction(root)
            self.assertTrue(transaction_path.exists())

    def test_ralph_prompt_skips_blocked_and_unmet_dependencies(self):
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        dd_010 = next(item for item in backlog["items"] if item["id"] == "DD-010")
        dd_010["status"] = "blocked"
        with self.assertRaisesRegex(HarnessError, "no executable PRDs"):
            show_ralph_prompt(root, backlog, None)

        dd_010["status"] = "ready"
        dd_010["depends_on"] = ["DD-009"]
        next(
            item for item in backlog["items"] if item["id"] == "DD-009"
        )["status"] = "blocked"
        with self.assertRaisesRegex(HarnessError, "no executable PRDs"):
            show_ralph_prompt(root, backlog, None)

    def test_ralph_prompt_rejects_unmet_dependencies(self):
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        next(
            item for item in backlog["items"] if item["id"] == "DD-001"
        )["status"] = "ready"
        next(
            item for item in backlog["items"] if item["id"] == "DD-002"
        )["status"] = "ready"
        with self.assertRaisesRegex(HarnessError, "DD-001"):
            show_ralph_prompt(root, backlog, "DD-002")

    def test_prd_metadata_must_match_backlog(self):
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        invalid = copy.deepcopy(backlog)
        next(
            item for item in invalid["items"] if item["id"] == "DD-010"
        )["priority"] = 99
        with self.assertRaisesRegex(HarnessError, "priority"):
            validate_backlog(invalid, root)

    def test_done_prd_cannot_contain_unchecked_work(self):
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        item = copy.deepcopy(
            next(item for item in backlog["items"] if item["id"] == "DD-010")
        )
        item["status"] = "done"
        text = (root / item["prd"]).read_text(encoding="utf-8")
        text = re.sub(
            r"^Status:.*$",
            "Status: Done  ",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        text = text.replace("- [x]", "- [ ]", 1)
        with self.assertRaisesRegex(HarnessError, "unchecked work"):
            validate_prd(item, text)


if __name__ == "__main__":
    unittest.main()
