#!/usr/bin/env python3
"""Read only the uniquely identified local Codex conversation, incrementally."""
import ast
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

MAX_LINE = 16 * 1024 * 1024
MAX_READ = 8 * 1024 * 1024
MAX_BLOCKS = 1500
MAX_TEXT = 4 * 1024 * 1024
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
MEMORY_CITATION = re.compile(r'<(?:oai-mem-citation|citation_entries|rollout_ids)\b[^>]*>.*?(?:</(?:oai-mem-citation|citation_entries|rollout_ids)\s*>|$)', re.I | re.S)


def visible_message(text):
    # These are UI metadata, not part of the assistant's rendered response.
    text = MEMORY_CITATION.sub('', text)
    text = re.sub(r'</?(?:oai-mem-citation|citation_entries|rollout_ids)\b[^>]*>', '', text, flags=re.I)
    return text.rstrip()


def visible_user_message(text):
    # UI question replies are transport envelopes, not literal user prose.
    if text.lstrip().startswith('<send_user_message_question_reply>'):
        match = re.fullmatch(r'\s*<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*', text, re.S)
        try:
            items = json.loads(match[1]) if match else []
        except (ValueError, RecursionError):
            items = []
        return '\n\n'.join(item['answer'] for item in items
                            if isinstance(item, dict) and isinstance(item.get('answer'), str)) if isinstance(items, list) else ''
    # The text envelope describes an attachment also present as input_image.
    # Keep exactly one clickable badge and the user's caption, never its path.
    attachment = re.compile(r'<image\s+name=(?P<quote>["\']?)\[Image #(?P<index>\d+)\](?P=quote)[^>]*>.*?</image>', re.S)
    indices = list(dict.fromkeys(match["index"] for match in attachment.finditer(text)))
    text = attachment.sub('', text).strip()
    missing = [f'[Image #{index}]' for index in indices if f'[Image #{index}]' not in text]
    return (' '.join(missing) + ('\n' if missing and text else '') + text).strip()


def clean(value, limit=256 * 1024):
    text = ANSI.sub('', str(value)).replace('\r\n', '\n').replace('\r', '\n')
    text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)
    if len(text) > limit:
        text = text[:limit] + '\n[Remaining content omitted from this view]'
    return text


def output_text(value, depth=0):
    if depth > 5:
        return clean(value, 64 * 1024)
    if isinstance(value, list):
        return '\n'.join(output_text(item, depth + 1) for item in value)
    if isinstance(value, dict):
        if value.get('type') in ('image', 'input_image', 'audio', 'input_audio'):
            return '[Attachment]'
        if 'text' in value:
            return output_text(value['text'], depth + 1)
        if 'output' in value:
            return output_text(value['output'], depth + 1)
        return clean(json.dumps(value, ensure_ascii=False), 64 * 1024)
    if isinstance(value, str) and len(value) <= 1024 * 1024 and value[:1] in ('[', '{'):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError, RecursionError, MemoryError):
                parsed = None
        if isinstance(parsed, (list, dict)):
            return output_text(parsed, depth + 1)
    return clean(value if value is not None else '', 64 * 1024)


def call_summary(name, arguments):
    if 'apply_patch' in name or 'apply_patch(' in arguments or arguments.startswith('*** Begin Patch'):
        files = re.findall(r'\*\*\* (?:Update|Add|Delete) File:', arguments)
        return 'Changes' + (f' · {len(files)} ' + ('file' if len(files) == 1 else 'files') if files else ' · file edits')
    try:
        data = json.loads(arguments)
    except (ValueError, TypeError):
        data = {}
    command = data.get('cmd') or data.get('command') or data.get('code') if isinstance(data, dict) else None
    if isinstance(command, list):
        command = ' '.join(str(item) for item in command)
    if not command:
        match = re.search(r'\b(?:cmd|command)\s*:\s*("(?:[^"\\]|\\.)*")', arguments)
        if match:
            try:
                command = json.loads(match[1])
            except ValueError:
                pass
    detail = clean(command or arguments, 200).splitlines()
    detail = detail[0].strip() if detail else ''
    return clean(name + (' · ' + detail if detail else ''), 200)


class Session:
    def __init__(self, session_id, path, cwd):
        self.session_id = session_id
        self.path = path
        self.cwd = cwd
        self.offset = 0
        self.identity = None
        self.verified = False
        self.blocks = []
        self.by_id = {}
        self.calls = {}
        self.revision = 0
        self.last_sent = -1

    def add(self, block):
        previous = self.by_id.get(block['id'])
        if previous is not None:
            previous.update(block)
        else:
            self.blocks.append(block)
            self.by_id[block['id']] = block
        self.revision += 1

    def event(self, event, position):
        payload = event.get('payload') or {}
        if not isinstance(payload, dict):
            return
        kind = event.get('type')
        if kind == 'session_meta':
            self.verified = payload.get('id') == self.session_id and payload.get('cwd') == self.cwd
            return
        if not self.verified:
            return
        if kind == 'event_msg' and payload.get('type') == 'turn_aborted':
            for block in self.calls.values():
                if block.get('pending'):
                    block['pending'] = False
                    block['output'] += '\n[Interrupted]'
                    self.revision += 1
            return
        if kind != 'response_item':
            return
        item_type = payload.get('type')
        if item_type == 'message':
            role = payload.get('role')
            # Never surface instructions or reasoning from the session log.
            if role not in ('user', 'assistant') or payload.get('channel') in ('analysis', 'justify', 'confidence') or payload.get('phase') == 'analysis':
                return
            content = payload.get('content') or []
            texts = [str(item.get('text', '')) for item in content if item.get('type') in ('input_text', 'output_text', 'text')]
            text = '\n'.join(texts).strip()
            if role == 'assistant':
                text = visible_message(text)
            else:
                text = visible_user_message(text)
            if role == 'user' and text.startswith(('<environment_context>', '<turn_aborted>', '<permissions instructions>')):
                return
            images = sum(item.get('type') in ('input_image', 'image') for item in content)
            if images and not re.search(r'\[Image #\d+\]', text):
                text = ' '.join(f'[Image #{n + 1}]' for n in range(images)) + ('\n' + text if text else '')
            if not text:
                return
            self.add({'id': payload.get('id') or f'message:{position}', 'kind': role, 'text': clean(text), 'summary': '', 'output': '', 'pending': False})
        elif item_type in ('function_call', 'custom_tool_call'):
            call_id = payload.get('call_id') or payload.get('id') or f'call:{position}'
            arguments = payload.get('arguments', payload.get('input', ''))
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            name = str(payload.get('name') or 'Tool')
            details = arguments
            patches = re.findall(r'apply_patch\(\s*("(?:[^"\\]|\\.)*")\s*\)', arguments)
            if patches:
                try:
                    details = '\n\n'.join(json.loads(patch) for patch in patches)
                except (ValueError, TypeError):
                    pass
            block = {'id': 'tool:' + call_id, 'kind': 'tool', 'text': clean(details, 64 * 1024), 'summary': call_summary(name, arguments), 'output': '', 'pending': True}
            self.add(block)
            self.calls[call_id] = self.by_id[block['id']]
        elif item_type in ('function_call_output', 'custom_tool_call_output'):
            call_id = payload.get('call_id')
            block = self.calls.get(call_id)
            if block is None:
                # Outputs without their call must still be visible and foldable.
                block = {'id': f'output:{call_id or position}', 'kind': 'tool', 'text': '', 'summary': 'Tool output', 'output': '', 'pending': False}
                self.add(block)
            block['output'] = output_text(payload.get('output'))
            block['pending'] = False
            self.revision += 1

    def trim(self):
        size = 0
        keep = []
        for block in reversed(self.blocks[-MAX_BLOCKS:]):
            size += len(block['text']) + len(block['output'])
            if size > MAX_TEXT and keep:
                break
            keep.append(block)
        self.blocks = list(reversed(keep))
        self.by_id = {block['id']: block for block in self.blocks}
        self.calls = {key: block for key, block in self.calls.items() if block['id'] in self.by_id}

    def read(self):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if self.identity != identity or stat.st_size < self.offset:
            self.__init__(self.session_id, self.path, self.cwd)
            self.identity = identity
        if self.offset == stat.st_size and self.last_sent == self.revision:
            return {'unchanged': True}
        consumed = 0
        with self.path.open('rb') as handle:
            handle.seek(self.offset)
            while consumed < MAX_READ:
                position = handle.tell()
                line = handle.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    while line and not line.endswith(b'\n'):
                        line = handle.readline(MAX_LINE + 1)
                    self.offset = handle.tell()
                    consumed += self.offset - position
                    continue
                if not line.endswith(b'\n'):
                    break  # An append in progress is read again on the next poll.
                self.offset = handle.tell()
                consumed += len(line)
                try:
                    self.event(json.loads(line), position)
                except (ValueError, TypeError, AttributeError, RecursionError):
                    continue
        self.trim()
        if not self.verified:
            return {'ready': False}
        if self.offset < stat.st_size:
            return {'unchanged': True} if self.last_sent >= 0 else {'ready': False}
        self.last_sent = self.revision
        return {'ready': True, 'session_id': self.session_id, 'revision': self.revision, 'blocks': self.blocks}


class Reader:
    def __init__(self, home):
        self.home = Path(home)
        self.sessions = {}
        self.target = None

    def read(self, request):
        title = str(request.get('title') or '').rsplit(' | ', 1)[0].strip()
        cwd = request.get('cwd')
        if not title or not cwd:
            return {'ready': False}
        with sqlite3.connect((self.home / 'state_5.sqlite').as_uri() + '?mode=ro', uri=True, timeout=0.2) as db:
            rows = db.execute('SELECT id, rollout_path, cwd FROM threads WHERE name = ? AND cwd = ? AND archived = 0 LIMIT 2', (title, cwd)).fetchall()
        if len(rows) != 1:
            return {'ready': False}
        session_id, path, cwd = rows[0]
        if session_id not in self.sessions:
            if len(self.sessions) >= 4:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[session_id] = Session(session_id, Path(path), cwd)
        target = (request.get('pane_id'), request.get('title'), cwd, request.get('boot_id'))
        if self.target != target:
            self.sessions[session_id].last_sent = -1
            self.target = target
        result = self.sessions[session_id].read()
        result['pane_id'] = request.get('pane_id')
        result['title'] = request.get('title')
        result['cwd'] = cwd
        return result


def main():
    reader = Reader(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    for line in sys.stdin:
        try:
            request = json.loads(line[:32768])
            result = reader.read(request)
        except (OSError, ValueError, sqlite3.Error, RecursionError):
            result = {'ready': False}
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
