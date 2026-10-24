import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('reader', Path(__file__).with_name('reader.py'))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.path = self.home / 'rollout.jsonl'
        self.path.write_text('')
        self.session = reader.Session('session-1', self.path, '/work')
        self.append('session_meta', {'id': 'session-1', 'cwd': '/work'})

    def tearDown(self):
        self.temp.cleanup()

    def append(self, kind, payload):
        with self.path.open('a') as handle:
            handle.write(json.dumps({'type': kind, 'payload': payload}) + '\n')

    def message(self, role, text, **extra):
        self.append('response_item', dict(type='message', role=role, content=[{'type': 'input_text', 'text': text}], **extra))

    def test_typed_blocks_preserve_messages_and_pair_outputs(self):
        self.message('user', 'Please inspect this file')
        self.message('assistant', 'I will inspect it.', phase='commentary')
        self.append('response_item', {'type': 'function_call', 'call_id': 'call-1', 'name': 'exec_command', 'arguments': json.dumps({'cmd': 'cat file.txt'})})
        self.append('response_item', {'type': 'function_call_output', 'call_id': 'call-1', 'output': json.dumps({'output': 'file contents', 'exit_code': 0})})
        self.message('assistant', 'Here is the answer.', phase='final')
        blocks = self.session.read()['blocks']
        self.assertEqual([b['kind'] for b in blocks], ['user', 'assistant', 'tool', 'assistant'])
        self.assertEqual(blocks[2]['output'], 'file contents')
        self.assertIn('cat file.txt', blocks[2]['summary'])
        self.assertFalse(blocks[2]['pending'])
        self.assertEqual(blocks[3]['text'], 'Here is the answer.')

    def test_instructions_reasoning_and_environment_are_never_rendered(self):
        for role in ('system', 'developer'):
            self.message(role, 'secret instructions')
        self.message('assistant', 'hidden reasoning', channel='analysis')
        self.append('response_item', {'type': 'reasoning', 'summary': [{'text': 'hidden reasoning'}]})
        self.message('user', '<environment_context>cwd</environment_context>')
        self.message('assistant', 'Visible answer', phase='final')
        blocks = self.session.read()['blocks']
        self.assertEqual([b['text'] for b in blocks], ['Visible answer'])

    def test_custom_tools_and_literal_content_outputs(self):
        self.append('response_item', {'type': 'custom_tool_call', 'call_id': 'c', 'name': 'exec', 'input': 'text(await tools.exec_command({cmd:"ls -l"}));'})
        self.append('response_item', {'type': 'custom_tool_call_output', 'call_id': 'c', 'output': repr([{'type': 'input_text', 'text': json.dumps({'output': 'Äpfel\nsecond line'})}])})
        block = self.session.read()['blocks'][0]
        self.assertIn('ls -l', block['summary'])
        self.assertEqual(block['output'], 'Äpfel\nsecond line')

    def test_incomplete_appends_and_late_output_keep_stable_ids(self):
        self.append('response_item', {'type': 'function_call', 'call_id': 'c', 'name': 'run', 'arguments': '{}'})
        first = self.session.read()
        self.assertTrue(first['blocks'][0]['pending'])
        payload = json.dumps({'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'c', 'output': 'done'}})
        with self.path.open('a') as handle:
            handle.write(payload[:25])
        self.session.read()
        with self.path.open('a') as handle:
            handle.write(payload[25:] + '\n')
        second = self.session.read()
        self.assertEqual(first['blocks'][0]['id'], second['blocks'][0]['id'])
        self.assertEqual(second['blocks'][0]['output'], 'done')
        self.assertEqual(self.session.read(), {'unchanged': True})

    def test_session_identity_must_match(self):
        self.session = reader.Session('another-session', self.path, '/work')
        self.message('user', 'Must not leak to another pane')
        self.assertFalse(self.session.read()['ready'])

    def test_internal_citations_and_incomplete_citation_blocks_are_hidden(self):
        self.message('assistant', 'Visible answer.\n<oai-mem-citation>\n<citation_entries>\nMEMORY.md:1-2|note=[internal]\n</citation_entries>\n<rollout_ids>id</rollout_ids>\n</oai-mem-citation>', phase='final')
        self.message('assistant', 'Another answer.\n<oai-mem-citation>\nMEMORY.md:3-4', phase='final')
        blocks = self.session.read()['blocks']
        self.assertEqual([block['text'] for block in blocks], ['Visible answer.', 'Another answer.'])

    def test_question_replies_render_only_the_users_answer(self):
        items = [{'answer': 'Ich verwende meine Advantage 360.', 'question': 'Welche Tastatur?', 'questionItemId': 'private-id'}, {'answer': 'Nein, rechts geht es auch nicht.'}]
        self.message('user', '<send_user_message_question_reply>\n' + json.dumps(items) + '\n</send_user_message_question_reply>')
        self.message('user', '<send_user_message_question_reply>invalid')
        self.message('user', 'A normal message with {"answer": "literal JSON"}')
        blocks = self.session.read()['blocks']
        self.assertEqual([block['text'] for block in blocks], ['Ich verwende meine Advantage 360.\n\nNein, rechts geht es auch nicht.', 'A normal message with {"answer": "literal JSON"}'])

    def test_image_envelopes_keep_one_badge_caption_and_preview_context(self):
        text = '<image name="[Image #1]" path="/private/tmp/screenshot.jpg">\n</image>[Image #1] diese antwort schaut kaputt aus?'
        self.message('user', text)
        self.message('user', '<image name="[Image #2]" path="/private/tmp/two.jpg">\n</image>')
        blocks = self.session.read()['blocks']
        self.assertEqual(blocks[0]['text'], '[Image #1] diese antwort schaut kaputt aus?')
        self.assertEqual(blocks[1]['text'], '[Image #2]')
        # The installed image resolver removes envelopes and badges before
        # comparing the visible caption against the original rollout message.
        def normalized(value):
            import re
            return re.sub(r'\s+', ' ', re.sub(r'<image\b[^>]*>|</image>|\[Image #\d+\]', '', value)).strip(' ›>\n')
        self.assertEqual(normalized(text), normalized(blocks[0]['text']))

    def test_real_unquoted_and_single_quoted_image_envelopes_are_not_duplicated(self):
        for i, name in enumerate(('[Image #1]', "'[Image #1]'", '"[Image #1]"')):
            self.message('user', f'<image name={name} path="/private/tmp/image.jpg">\n</image>[Image #1] Same caption', id=f'image-{i}')
        blocks = self.session.read()['blocks']
        self.assertEqual([block['text'] for block in blocks], ['[Image #1] Same caption'] * 3)

    def test_changes_are_tool_blocks_with_readable_patch_details(self):
        patch = '*** Begin Patch\n*** Update File: file.rs\n@@\n-old\n+new\n*** End Patch'
        code = 'text(await tools.apply_patch(' + json.dumps(patch) + '));'
        self.append('response_item', {'type': 'custom_tool_call', 'call_id': 'edit', 'name': 'exec', 'input': code})
        block = self.session.read()['blocks'][0]
        self.assertEqual(block['kind'], 'tool')
        self.assertEqual(block['summary'], 'Changes · 1 file')
        self.assertIn('\n-old\n+new\n', block['text'])

    def test_opening_combined_changes_preserves_every_patch(self):
        first = '*** Begin Patch\n*** Update File: first.rs\n-old\n+new\n*** End Patch'
        second = '*** Begin Patch\n*** Update File: second.rs\n-before\n+after\n*** End Patch'
        code = 'text(await tools.apply_patch(' + json.dumps(first) + '));\ntext(await tools.apply_patch(' + json.dumps(second) + '));'
        self.append('response_item', {'type':'custom_tool_call','call_id':'combined','name':'exec','input':code})
        block = self.session.read()['blocks'][0]
        self.assertEqual(block['summary'], 'Changes · 2 files')
        self.assertIn(first, block['text'])
        self.assertIn(second, block['text'])

    def test_title_and_cwd_must_resolve_to_exactly_one_thread(self):
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('create table threads (id text, rollout_path text, cwd text, name text, archived integer)')
            db.execute('insert into threads values (?, ?, ?, ?, 0)', ('session-1', str(self.path), '/work', 'Title'))
        self.message('user', 'Visible')
        instance = reader.Reader(self.home)
        request = {'pane_id': 'w1:p1', 'title': 'Title | herrsch', 'cwd': '/work'}
        self.assertTrue(instance.read(request)['ready'])
        self.assertTrue(instance.read(dict(request, pane_id='another-pane'))['ready'])
        self.assertFalse(instance.read(dict(request, cwd='/other'))['ready'])
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('insert into threads values (?, ?, ?, ?, 0)', ('session-2', str(self.path), '/work', 'Title'))
        self.assertFalse(instance.read(request)['ready'])

    def test_return_to_quiet_session_always_resends_document(self):
        self.message('user', 'First conversation')
        other = self.home / 'other.jsonl'
        other.write_text(json.dumps({'type':'session_meta','payload':{'id':'session-2','cwd':'/work'}}) + '\n' + json.dumps({'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'Second conversation'}]}}) + '\n')
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('create table threads (id text, rollout_path text, cwd text, name text, archived integer)')
            db.executemany('insert into threads values (?, ?, ?, ?, 0)', [('session-1',str(self.path),'/work','First'), ('session-2',str(other),'/work','Second')])
        instance = reader.Reader(self.home)
        first = {'pane_id':'one','title':'First | herrsch','cwd':'/work'}
        second = {'pane_id':'two','title':'Second | herrsch','cwd':'/work'}
        self.assertTrue(instance.read(first)['ready'])
        self.assertTrue(instance.read(first)['unchanged'])
        self.assertTrue(instance.read(second)['ready'])
        returned = instance.read(first)
        self.assertTrue(returned['ready'])
        self.assertEqual(returned['blocks'][0]['text'], 'First conversation')

    def test_interrupt_and_truncation_are_handled(self):
        self.append('response_item', {'type': 'function_call', 'call_id': 'c', 'name': 'run', 'arguments': '{}'})
        self.append('event_msg', {'type': 'turn_aborted'})
        block = self.session.read()['blocks'][0]
        self.assertFalse(block['pending'])
        self.assertIn('Interrupted', block['output'])
        self.path.write_text('')
        self.append('session_meta', {'id': 'session-1', 'cwd': '/work'})
        self.message('user', 'New session content')
        blocks = self.session.read()['blocks']
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]['kind'], 'user')


if __name__ == '__main__':
    unittest.main()
