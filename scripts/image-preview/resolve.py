#!/usr/bin/env python3
"""Resolve a clicked Codex attachment locally; never fetch remote image URLs."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import sys
from video_attachments import register as register_video, resolve as resolve_video

MAX_IMAGE = 24 * 1024 * 1024


def normalized(text):
    text = re.sub(r'<image\b[^>]*>|</image>|\[Image #\d+\]', '', text)
    return re.sub(r'\s+', ' ', text).strip(' ›>\n')


def session_for(request, home):
    title = str(request.get('title') or '').rsplit(' | ', 1)[0].strip()
    if not title:
        return None
    database = home / 'state_5.sqlite'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
        rows = db.execute('SELECT rollout_path, cwd FROM threads WHERE name = ? AND archived = 0 ORDER BY updated_at DESC LIMIT 3', (title,)).fetchall()
    matching = [Path(path) for path, cwd in rows if not request.get('cwd') or cwd == request['cwd']]
    return matching[0] if len(matching) == 1 else None


def attachment(request, session):
    index = int(request.get('index', 1)) - 1
    context = normalized(request.get('context', ''))
    matches = []
    with session.open(encoding='utf-8') as handle:
        for line in handle:
            if len(line) > 100 * 1024 * 1024:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get('payload', {})
            if event.get('type') != 'response_item' or payload.get('role') != 'user':
                continue
            content = payload.get('content', [])
            images = [c.get('image_url', '') for c in content if c.get('type') == 'input_image']
            if index >= len(images) or index < 0:
                continue
            text = normalized(' '.join(c.get('text', '') for c in content if c.get('type') == 'input_text'))
            # Visible text can wrap after the image badge. Only match this session's
            # own user messages, never tool outputs or another terminal's latest image.
            if (context and len(context) >= 6 and context in text) or (not context and not text):
                matches.append(images[index])
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def image_bytes(url):
    match = re.fullmatch(r'data:image/(png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=\r\n]+)', url)
    if not match or len(match[2]) > MAX_IMAGE * 4 // 3 + 100:
        raise ValueError('This attachment is not a supported local image.')
    value = base64.b64decode(match[2], validate=True)
    if len(value) > MAX_IMAGE:
        raise ValueError('This image is too large for the preview.')
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--register-video', action='store_true')
    parser.add_argument('--request')
    parser.add_argument('--request-stdin', action='store_true')
    parser.add_argument('--file')
    parser.add_argument('--resolve-only', action='store_true')
    args = parser.parse_args()
    if args.register_video:
        try:
            request = json.loads(sys.stdin.read(300000))
            print(json.dumps(register_video(request)))
        except (OSError, ValueError, KeyError) as error:
            print(json.dumps({'error': str(error)}))
        return
    panel = Path(__file__).with_name('HerdrImagePreview.app')
    temp = None
    mode = '--file'
    try:
        if args.file:
            path = str(Path(args.file).resolve(strict=True))
        else:
            request = json.loads(sys.stdin.read(MAX_IMAGE * 4 // 3 + 65536) if args.request_stdin else args.request)
            if request.get('media') == 'video':
                path = resolve_video(request)
                if args.resolve_only:
                    print(path)
                    return
                subprocess.run(['/usr/bin/open', '-n', '-W', str(panel), '--args', '--video', path], check=True)
                return
            url = request.get('draft_image')
            if not url:
                if request.get('draft'):
                    raise ValueError('This draft image was pasted before the preview client was attached, or the attachment list changed. Copy and paste the image again to preview it.')
                home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
                session = session_for(request, home)
                if not session:
                    raise ValueError('The Codex session for this terminal could not be identified. Open the preview after Codex has assigned the conversation a title.')
                url = attachment(request, session)
                if not url:
                    raise ValueError('This image could not be matched to a unique message in this conversation.')
            fd, temp = tempfile.mkstemp(prefix='herdr-preview-')
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(image_bytes(url))
            path = temp
        if args.resolve_only:
            print(path)
            return
        subprocess.run(['/usr/bin/open', '-n', '-W', str(panel), '--args', '--file', path], check=True)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        if args.resolve_only:
            raise
        subprocess.run(['/usr/bin/open', '-n', '-W', str(panel), '--args', '--error', str(error)], check=False)
    finally:
        if temp and not args.resolve_only:
            Path(temp).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
