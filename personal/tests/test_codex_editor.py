import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("codex_editor", Path(__file__).resolve().parents[1] / "codex_editor.py")
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)


class DraftEditorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "requests"
        self.home = self.base / "codex"
        self.draft = self.home / "editor/.tmp-test.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_text("Zitat\n\nMein Kommentar mit ä", encoding="utf-8")
        self.token = "a" * 32
        patch = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home), "HERDR_PANE_ID": "w1:p1",
            "HERDR_SOCKET_PATH": "test.sock", "HERDR_DRAFT_EDITOR_TOKEN": self.token})
        patch.start();self.addCleanup(patch.stop)
        self.request = self.root / (editor.identity("test.sock", "w1:p1") + ".request")

    def request_clear(self, token=None, created_ms=100000):
        editor.write_private(self.request, {"token": token or self.token, "created_ms": created_ms})

    def test_clears_entire_multiline_draft_once_without_touching_other_files(self):
        other = self.base / "project.txt";other.write_text("keep")
        self.request_clear()
        self.assertTrue(editor.clear_requested_draft(self.draft, self.root, now=100))
        self.assertEqual(self.draft.read_text(), "")
        self.assertEqual(other.read_text(), "keep")
        self.assertFalse(self.request.exists())
        self.assertFalse(editor.clear_requested_draft(self.draft, self.root, now=100))

    def test_stale_or_other_process_request_does_not_clear_draft(self):
        for token, created in [("b" * 32, 100000), (self.token, 1000), (self.token, 200000)]:
            self.request_clear(token, created)
            self.assertFalse(editor.clear_requested_draft(self.draft, self.root, now=100))
            self.assertIn("Mein Kommentar", self.draft.read_text())

    def test_editor_cannot_clear_project_or_commit_files(self):
        for path in [self.base / "project.md", self.home / "editor/COMMIT_EDITMSG"]:
            path.write_text("keep")
            self.request_clear()
            self.assertFalse(editor.clear_requested_draft(path, self.root, now=100))
            self.assertEqual(path.read_text(), "keep")

    def test_regular_editor_shortcut_keeps_original_editor(self):
        with mock.patch.dict(os.environ, {"HERDR_ORIGINAL_VISUAL": "my-editor --wait"}), \
             mock.patch.object(editor, "clear_requested_draft", return_value=False), \
             mock.patch.object(editor.os, "execvp") as execute, \
             mock.patch.object(editor.subprocess, "call", return_value=0) as call:
            try:
                editor.edit([str(self.draft)])
            except SystemExit:
                pass
            if os.name == "nt":
                self.assertTrue(call.called)
            else:
                execute.assert_called_once_with("my-editor", ["my-editor", "--wait", str(self.draft)])


if __name__ == "__main__":
    unittest.main()
