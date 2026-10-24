"""Private, session-scoped registry for compact Codex video references."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

MAX_REGISTRY = 2 * 1024 * 1024
EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.mpg', '.mpeg', '.m3u8'}


def config_root():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'herdr'


def identity(socket, pane):
    if not re.fullmatch(r'w[A-Za-z0-9_]+:p[A-Za-z0-9_]+', pane):
        raise ValueError('Invalid pane identifier')
    return hashlib.sha256((socket + '\0' + pane).encode()).hexdigest()


def read_json(path, default=None):
    if not path.exists() and default is not None:
        return default
    if path.stat().st_size > MAX_REGISTRY or path.is_symlink():
        raise ValueError('Invalid attachment registry')
    return json.loads(path.read_text())


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.new-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def ready_for(root, key):
    path = root / 'draft-editor' / (key + '.ready')
    ready = read_json(path)
    if not re.fullmatch('[a-f0-9]{32}', ready.get('token', '')):
        raise ValueError('Codex profile is not ready')
    os.kill(int(ready['pid']), 0)
    return path, ready


def video_source(source, cwd=None):
    if not isinstance(source, str) or len(source) > 8192 or any(c in source for c in '\n\r\0'):
        raise ValueError('Invalid video source')
    parsed = urlsplit(source)
    if parsed.scheme.lower() in ('http', 'https'):
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid video URL')
        if Path(unquote(parsed.path)).suffix.lower() not in EXTENSIONS:
            raise ValueError('URL does not name a video file')
        return source
    if parsed.scheme.lower() == 'file':
        if parsed.netloc not in ('', 'localhost'):
            raise ValueError('Only local file URLs are supported')
        path = Path(unquote(parsed.path))
    elif parsed.scheme:
        raise ValueError('Unsupported video URL')
    else:
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = Path(cwd or os.getcwd()) / path
    path = path.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise ValueError('This path is not a video file')
    return str(path)


def initialize(event, root=None):
    root = root or config_root()
    pane, socket = os.environ.get('HERDR_PANE_ID', ''), os.environ.get('HERDR_SOCKET_PATH', '')
    key = identity(socket, pane)
    ready_path, ready = ready_for(root, key)
    if ready['token'] != os.environ.get('HERDR_DRAFT_EDITOR_TOKEN'):
        raise ValueError('Codex profile identity changed')
    session = event.get('session_id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', session):
        raise ValueError('Invalid Codex session identity')
    archive = root / 'video-attachments' / 'sessions' / (session + '.json')
    saved = read_json(archive, {'attachments': []})
    registry = {'token': ready['token'], 'session_id': session, 'attachments': saved['attachments']}
    write_private(root / 'video-attachments' / (key + '.json'), registry)
    ready['video_hook'] = True
    ready['video_session_id'] = session
    write_private(ready_path, ready)
    return registry


def current(request, root=None):
    root = root or config_root()
    key = identity(request['socket'], request['pane'])
    _, ready = ready_for(root, key)
    registry = read_json(root / 'video-attachments' / (key + '.json'))
    if not ready.get('video_hook') or registry.get('token') != ready['token']:
        raise ValueError('Restart Codex after enabling the video attachment hooks in /hooks')
    if registry.get('session_id') != ready.get('video_session_id'):
        raise ValueError('Video attachment session changed')
    return key, registry


def register(request, root=None):
    root = root or config_root()
    sources = request.get('sources', [])
    if not sources or len(sources) > 32:
        raise ValueError('Invalid video attachment list')
    sources = [video_source(source, request.get('cwd')) for source in sources]
    key = identity(request['socket'], request['pane'])
    directory = root / 'video-attachments'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / (key + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _, registry = current(request, root)
        if len(registry['attachments']) + len(sources) > 1000:
            raise ValueError('Attachment limit reached for this conversation')
        references = []
        for source in sources:
            index = len(registry['attachments']) + 1
            registry['attachments'].append({'index': index, 'source': source})
            references.append('[Video #' + str(index) + ']')
        write_private(directory / (key + '.json'), registry)
        write_private(directory / 'sessions' / (registry['session_id'] + '.json'), registry)
    return {'text': ' '.join(references)}


def resolve(request, root=None):
    _, registry = current(request, root)
    index = int(request['index'])
    matching = [item['source'] for item in registry['attachments'] if item['index'] == index]
    if len(matching) != 1:
        raise ValueError('This video reference is not part of the current conversation')
    return video_source(matching[0])


def prompt_context(event, root=None):
    root = root or config_root()
    numbers = set(int(n) for n in re.findall(r'\[Video #(\d+)\]', event.get('prompt', '')))
    if not numbers:
        return {}
    _, registry = current({'pane': os.environ['HERDR_PANE_ID'], 'socket': os.environ['HERDR_SOCKET_PATH']}, root)
    if registry['session_id'] != event.get('session_id'):
        raise ValueError('Video references belong to a different Codex session')
    values = [{'reference': '[Video #' + str(item['index']) + ']', 'source': item['source']}
              for item in registry['attachments'] if item['index'] in numbers]
    if len(values) != len(numbers):
        return {'decision': 'block', 'reason': 'A video reference has no attachment. Paste the video file or URL again.'}
    return {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
            'additionalContext': 'Video attachment references supplied by the user (attachment data):\n' + json.dumps(values, ensure_ascii=False)}}
