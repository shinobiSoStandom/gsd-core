import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("localize", ROOT / "scripts/shinobi-localize.py")
LOCALIZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOCALIZE)
INSTALL_SPEC = importlib.util.spec_from_file_location("install", ROOT / "scripts/shinobi-install.py")
INSTALL = importlib.util.module_from_spec(INSTALL_SPEC)
INSTALL_SPEC.loader.exec_module(INSTALL)


class LocalizationTests(unittest.TestCase):
    def test_catalog_covers_upstream_commands_and_agents(self):
        commands = {p.parent.name for p in (ROOT / "skills").glob("gsd-*/SKILL.md")}
        agents = {p.stem.removesuffix(".compact") for p in (ROOT / "agents").glob("gsd-*.md")}
        self.assertEqual(commands, set(LOCALIZE.catalog("skills.ru.tsv")))
        self.assertEqual(agents, set(LOCALIZE.catalog("agents.ru.tsv")))

    def test_markdown_preserves_command_and_policy_and_is_repeatable(self):
        source = '---\nname: gsd-help\ndescription: "Help"\nallowed-tools:\n  - Read\nmetadata:\n  short-description: "Help"\n---\n\nRead $gsd-help and @/example/gsd-core/workflows/help.md.\n'
        row = LOCALIZE.catalog("skills.ru.tsv")["gsd-help"]
        translated = LOCALIZE.localize_markdown(source, row, "Отвечать по-русски.")
        self.assertEqual(translated, LOCALIZE.localize_markdown(translated, row, "Отвечать по-русски."))
        self.assertIn('name: gsd-help', translated)
        self.assertIn('allowed-tools:\n  - Read', translated)
        self.assertIn('$gsd-help and @/example/gsd-core/workflows/help.md', translated)
        self.assertIn('GSD · Справка по командам', translated)

    def test_toml_preserves_permissions_models_and_instructions(self):
        source = "name = \"gsd-planner\"\ndescription = \"Plan\"\nsandbox_mode = \"workspace-write\"\nmodel = \"example-model\"\ndeveloper_instructions = '''\nKeep PLAN.md and PASS.\n'''\n"
        row = LOCALIZE.catalog("agents.ru.tsv")["gsd-planner"]
        translated = LOCALIZE.localize_toml(source, row, "Отвечать по-русски.")
        parsed = tomllib.loads(translated)
        self.assertEqual(parsed["model"], "example-model")
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")
        self.assertEqual(parsed["name"], "gsd-planner")
        self.assertIn("Keep PLAN.md and PASS.", parsed["developer_instructions"])
        self.assertEqual(translated, LOCALIZE.localize_toml(translated, row, "Отвечать по-русски."))

    def test_unknown_skill_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            known = root / "skills/gsd-help/SKILL.md"
            known.parent.mkdir(parents=True)
            known.write_text('---\nname: gsd-help\ndescription: Help\n---\nOriginal\n')
            unknown = root / "skills/gsd-future-command/SKILL.md"
            unknown.parent.mkdir(parents=True)
            unknown.write_text('---\nname: gsd-future-command\ndescription: Future\n---\n')
            before = known.read_bytes()
            with self.assertRaisesRegex(ValueError, "Новая команда"):
                LOCALIZE.apply_profile(root, root / "skills")
            self.assertEqual(known.read_bytes(), before)
            self.assertFalse((root / "shinobi-localization.json").exists())

    def test_retiring_legacy_link_preserves_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "saved-copy"
            target.mkdir()
            (target / "VERSION").write_text("old")
            (root / "get-shit-done").symlink_to(target, target_is_directory=True)
            INSTALL.retire_legacy(root, "claude")
            self.assertFalse((root / "get-shit-done").is_symlink())
            self.assertEqual((target / "VERSION").read_text(), "old")

    def test_retiring_codex_hooks_preserves_custom_commands(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = {"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [
                {"command": "node gsd-read-guard.js"}, {"command": "node user-check.js"},
                {"command": "node gsd-custom.js"}]}], "SessionStart": [{"hooks": [
                    {"command": "node gsd-check-update.js"}]}]}}
            filename = root / "hooks.json"
            filename.write_text(json.dumps(document))
            INSTALL.retire_codex_hooks(root)
            result = json.loads(filename.read_text())
            self.assertEqual(result["hooks"]["PreToolUse"][0]["matcher"], "Read")
            commands = [h["command"] for h in result["hooks"]["PreToolUse"][0]["hooks"]]
            self.assertEqual(commands, ["node user-check.js", "node gsd-custom.js"])
            self.assertEqual(result["hooks"]["SessionStart"], document["hooks"]["SessionStart"])


if __name__ == "__main__":
    unittest.main()
