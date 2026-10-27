import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("context_hook", ROOT / "personalization/codex_context.py")
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)
SETUP_SPEC = importlib.util.spec_from_file_location("context_setup", ROOT / "scripts/shinobi-context-setup.py")
SETUP = importlib.util.module_from_spec(SETUP_SPEC)
SETUP_SPEC.loader.exec_module(SETUP)


class ContextTests(unittest.TestCase):
    def test_compaction_snapshot_recovers_and_prefers_live_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            note = root / ".planning/STATE.md"
            note.parent.mkdir(parents=True)
            note.write_text("Цель: подключить форму. Проверка доставки ещё не выполнена.")
            cache = Path(directory) / "cache"
            event = {"cwd": str(root), "hook_event_name": "PreCompact"}
            self.assertIsNone(HOOK.handle(event, cache))
            event["hook_event_name"] = "SessionStart"
            event["source"] = "compact"
            note.write_text("Новая цель: проверить получение заявки.")
            result = HOOK.handle(event, cache)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Новая цель", result)
            self.assertNotIn("Цель: подключить", result)
            note.unlink()
            restored = HOOK.handle(event, cache)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("прежний снимок", restored)
            self.assertIn("Цель: подключить", restored)

    def test_notes_cannot_read_symlink_outside_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            outside = Path(directory) / "outside.md"
            outside.write_text("private-outside-note")
            (root / "README.md").symlink_to(outside)
            result = HOOK.handle({"cwd": str(root), "hook_event_name": "SessionStart"}, Path(directory) / "cache")
            self.assertNotIn("private-outside-note", json.dumps(result))

    def test_output_is_bounded_and_does_not_create_project_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            (root / "README.md").write_text("Сведения о проекте. " * 10000)
            output = HOOK.handle({"cwd": str(root), "hook_event_name": "SessionStart"}, Path(directory) / "cache")
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertLessEqual(len(context), HOOK.MAX_CHARS)
            self.assertIn("выдержка", context)
            self.assertFalse((root / ".planning").exists())
            self.assertFalse((root / ".codex").exists())

    def test_subagent_gets_no_automatic_extra_context(self):
        with tempfile.TemporaryDirectory() as directory:
            result = HOOK.handle({"cwd": directory, "hook_event_name": "SubagentStart", "agent_type": "gsd-planner"}, Path(directory) / "cache")
            self.assertIsNone(result)

    def test_empty_project_adds_no_model_context(self):
        with tempfile.TemporaryDirectory() as directory:
            result = HOOK.handle({"cwd": directory, "hook_event_name": "SessionStart"}, Path(directory) / "cache")
            self.assertIsNone(result)

    def test_registration_is_repeatable_and_does_not_write_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "existing-user-hook"}]}]}}
            (root / "hooks.json").write_text(json.dumps(original))
            (root / "config.toml").write_text("# user settings and trust must stay unchanged\n")
            SETUP.install(root)
            first = (root / "hooks.json").read_bytes()
            SETUP.install(root)
            self.assertEqual(first, (root / "hooks.json").read_bytes())
            self.assertIn("existing-user-hook", first.decode())
            self.assertEqual((root / "config.toml").read_text(), "# user settings and trust must stay unchanged\n")


if __name__ == "__main__":
    unittest.main()
