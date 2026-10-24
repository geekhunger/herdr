import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import video_attachments as video
import video_hook


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'herdr'
        self.pane = 'w1:p1'
        self.socket = '/tmp/herdr-video-test.sock'
        self.key = video.identity(self.socket, self.pane)
        self.token = 'a' * 32
        self.env = patch.dict(os.environ, {'HERDR_ENV':'1', 'HERDR_PANE_ID': self.pane, 'HERDR_SOCKET_PATH':self.socket, 'HERDR_DRAFT_EDITOR_TOKEN':self.token, 'XDG_CONFIG_HOME':self.temp.name})
        self.env.start()
        video.write_private(self.root/'draft-editor'/(self.key+'.ready'), {'pid': os.getpid(), 'token':self.token})
        self.event = {'session_id':'test-session-1', 'hook_event_name':'SessionStart'}
        self.path = Path(self.temp.name) / '# Szene ä.MOV'
        self.path.write_bytes(b'fixture video')
        self.request = {'pane':self.pane,'socket':self.socket,'sources':[str(self.path)]}
    def tearDown(self):
        self.env.stop(); self.temp.cleanup()
    def test_uninitialized_profile_preserves_original_path_instead_of_orphan_reference(self):
        with self.assertRaises((ValueError, OSError)):
            video.register(self.request,self.root)
    def test_multiple_references_remain_unique_and_resume_restores_sources(self):
        video.initialize(self.event,self.root)
        self.assertEqual(video.register(self.request,self.root)['text'],'[Video #1]')
        self.assertEqual(video.register(dict(self.request,sources=[self.path.as_uri(),'https://example.com/movie.mp4?signature=abc']),self.root)['text'],'[Video #2] [Video #3]')
        video.initialize(self.event,self.root)
        self.assertEqual(video.resolve(dict(self.request,index=2),self.root),str(self.path.resolve()))
        self.assertEqual(video.resolve(dict(self.request,index=3),self.root),'https://example.com/movie.mp4?signature=abc')
        self.assertEqual(video.register(self.request,self.root)['text'],'[Video #4]')
    def test_context_contains_only_referenced_sources_and_blocks_missing_markers(self):
        video.initialize(self.event,self.root); video.register(self.request,self.root)
        event=dict(self.event,hook_event_name='UserPromptSubmit',prompt='Please inspect [Video #1]')
        result=video.prompt_context(event,self.root)
        context=result['hookSpecificOutput']['additionalContext']
        self.assertIn(str(self.path),context)
        self.assertIn('[Video #1]',context)
        self.assertEqual(video.prompt_context(dict(event,prompt='hello'),self.root),{})
        self.assertEqual(video.prompt_context(dict(event,prompt='[Video #2]'),self.root)['decision'],'block')
        with self.assertRaises(ValueError):
            video.prompt_context(dict(event,session_id='another-session'),self.root)
    def test_changed_session_identity_cannot_resolve_old_video(self):
        video.initialize(self.event,self.root); video.register(self.request,self.root)
        video.write_private(self.root/'draft-editor'/(self.key+'.ready'),{'pid':os.getpid(),'token':'b'*32})
        with self.assertRaises(ValueError): video.resolve(dict(self.request,index=1),self.root)
    def test_invalid_urls_and_removed_files_fail_without_fetching(self):
        for value in ['javascript:alert(1)', 'https://example.com/watch?v=123', 'file://remote/path/movie.mp4', 'https://name:password@example.com/movie.mp4']:
            with self.assertRaises(ValueError): video.video_source(value)
        video.initialize(self.event,self.root); video.register(self.request,self.root)
        self.path.unlink()
        with self.assertRaises(OSError): video.resolve(dict(self.request,index=1),self.root)
    def test_real_hook_entrypoint_outputs_codex_context(self):
        def run(event):
            output=io.StringIO()
            with patch('sys.stdin',io.StringIO(json.dumps(event))), contextlib.redirect_stdout(output): video_hook.main()
            return output.getvalue()
        self.assertEqual(run(self.event),'')
        video.register(self.request,self.root)
        output=json.loads(run(dict(self.event,hook_event_name='UserPromptSubmit',prompt='[Video #1]')))
        self.assertEqual(output['hookSpecificOutput']['hookEventName'],'UserPromptSubmit')
        self.assertIn(str(self.path),output['hookSpecificOutput']['additionalContext'])
        with patch.dict(os.environ,{'HERDR_ENV':'0'}): self.assertEqual(run(self.event),'')

if __name__ == '__main__': unittest.main()
