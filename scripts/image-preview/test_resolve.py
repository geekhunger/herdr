import base64
import importlib.util
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('resolve', Path(__file__).with_name('resolve.py'))
resolve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolve)

class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.session = self.root / 'session.jsonl'
        self.session.write_text('')
    def tearDown(self):
        self.temp.cleanup()
    def message(self, text, images, role='user'):
        content = [{'type':'input_text', 'text':text}] + [{'type':'input_image', 'image_url':image} for image in images]
        with self.session.open('a') as handle:
            handle.write(json.dumps({'type':'response_item','payload':{'role':role,'content':content}})+'\n')
    def test_correct_message_and_image_number(self):
        self.message('› [Image #1] first caption', ['one', 'two'])
        self.message('› [Image #1] second caption', ['other'])
        self.assertEqual(resolve.attachment({'index':2, 'context':'› [Image #2] first cap'}, self.session), 'two')
    def test_ambiguous_or_unmatched_click_does_not_show_another_image(self):
        self.message('[Image #1] same caption', ['one'])
        self.message('[Image #1] same caption', ['two'])
        self.assertIsNone(resolve.attachment({'index':1,'context':'same caption'}, self.session))
        self.assertIsNone(resolve.attachment({'index':1,'context':'unsent image'}, self.session))
    def test_ignores_assistant_and_remote_urls(self):
        self.message('caption here', ['one'], 'assistant')
        self.assertIsNone(resolve.attachment({'index':1,'context':'caption here'}, self.session))
        with self.assertRaises(ValueError):
            resolve.image_bytes('https://example.com/image.png')
        self.assertEqual(resolve.image_bytes('data:image/png;base64,'+base64.b64encode(b'image bytes').decode()), b'image bytes')
    def test_large_draft_uses_real_stdin_handoff_and_preserves_exact_bytes(self):
        data = bytes(range(256)) * 2048
        request = {'draft': True, 'draft_image': 'data:image/png;base64,' + base64.b64encode(data).decode()}
        result = subprocess.run(
            [sys.executable, str(Path(resolve.__file__)), '--request-stdin', '--resolve-only'],
            input=json.dumps(request), text=True, capture_output=True, check=True,
        )
        path = Path(result.stdout.strip())
        try:
            self.assertEqual(path.read_bytes(), data)
            if sys.platform != 'win32':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        finally:
            path.unlink(missing_ok=True)

    def test_exact_session_title_and_directory(self):
        db = sqlite3.connect(self.root / 'state_5.sqlite')
        db.execute('create table threads (rollout_path text, cwd text, name text, archived integer, updated_at integer)')
        db.execute('insert into threads values (?, ?, ?, 0, 0)', (str(self.session), '/work', 'My conversation'))
        db.commit(); db.close()
        request = {'title':'My conversation | herrsch','cwd':'/work'}
        self.assertEqual(resolve.session_for(request,self.root),self.session)
        self.assertIsNone(resolve.session_for(dict(request,cwd='/other'),self.root))

if __name__ == '__main__': unittest.main()
