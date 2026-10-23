import importlib.util
import json
from pathlib import Path
import shlex
import unittest

PROFILE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("personal_setup", PROFILE / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ProfileTest(unittest.TestCase):
    def test_template_has_all_personal_bindings_without_machine_paths(self):
        template = (PROFILE / "config.toml.in").read_text(encoding="utf-8")
        for binding in ['prefix = "ctrl+f"', 'close_tab = "prefix+ctrl+w"',
                        'new_workspace = "prefix+ctrl+n"', 'close_workspace = "prefix+ctrl+q"',
                        'new_tab = "prefix+ctrl+t"', 'key = "prefix+c"',
                        'key = "prefix+a"', 'key = "prefix+w"', 'key = "prefix+t"']:
            self.assertIn(binding, template)
        self.assertNotIn("/Users/", template)
        rendered = setup.render_config(Path("/example home/python"), Path("/example home/schedules"))
        self.assertNotIn("@PICKER@", rendered)
        self.assertNotIn("@SCHEDULES@", rendered)
        for relative in ["profile/picker.py", "app/main.py"]:
            expected = json.dumps(str(Path("/example home/schedules") / relative))[1:-1]
            self.assertIn(expected, rendered)

    def test_codex_defaults_preserve_other_settings_and_credentials(self):
        original = 'model = "old"\nother = "keep"\n\n[mcp_servers.other]\nmodel = "untouched"\ntoken = "LOCAL_ONLY_TEST_SENTINEL"\n'
        updated = setup.merge_codex_defaults(original)
        self.assertIn('model = "gpt-6.1-sol"', updated)
        self.assertIn('model_reasoning_effort = "high"', updated)
        self.assertIn('other = "keep"', updated)
        self.assertIn('[mcp_servers.other]\nmodel = "untouched"\ntoken = "LOCAL_ONLY_TEST_SENTINEL"', updated)
        self.assertEqual(setup.merge_codex_defaults(updated), updated)

    def test_newline_binding_preserves_other_editor_actions_and_next_section(self):
        original = '[tui.keymap.editor]\nmove_left = "left"\ninsert_newline = [\n "ctrl-]", # quoted closing bracket\n "ctrl-j",\n]\n\n[mcp_servers.other]\ncommand = "keep"\n'
        updated = setup.merge_codex_keymap(original)
        self.assertIn('move_left = "left"', updated)
        self.assertIn('[mcp_servers.other]\ncommand = "keep"', updated)
        self.assertIn('"ctrl-enter"', updated)
        self.assertEqual(updated.count('insert_newline ='), 1)
        self.assertEqual(setup.merge_codex_keymap(updated), updated)

    def test_newline_binding_adds_to_existing_editor_section(self):
        original = '[tui.keymap.editor]\nmove_left = "left"\n\n[other]\nvalue = "keep"\n'
        updated = setup.merge_codex_keymap(original)
        self.assertEqual(updated.count('[tui.keymap.editor]'), 1)
        self.assertLess(updated.index('insert_newline ='), updated.index('[other]'))


if __name__ == "__main__":
    unittest.main()
