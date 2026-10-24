"""Install the local macOS image panel and tested Herdr client with backups."""
from datetime import datetime
import hashlib
import json
import shlex
import os
from pathlib import Path
import shutil
import subprocess

repo = Path(__file__).resolve().parents[2]
source = Path(__file__).resolve().parent
root = Path.home() / '.config/herdr'
destination = root / 'scripts/image-preview'
binary = Path.home() / '.local/share/herdr-schedules/bin/herdr'
backup = binary.parent.parent / 'backups' / ('image-preview-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
shutil.copy2(binary, backup / 'herdr')
shutil.copy2(root / 'config.toml', backup / 'config.toml')
if destination.exists():
    shutil.copytree(destination, backup / 'helper')
destination.mkdir(parents=True, exist_ok=True)
for name in ('resolve.py', 'Preview.swift', 'video_attachments.py', 'video_hook.py'):
    shutil.copy2(source / name, destination / name)
shutil.copytree(source / 'HerdrImagePreview.app', destination / 'HerdrImagePreview.app', dirs_exist_ok=True)
# Cloud-synced source folders can attach Finder metadata after a build. Sign the
# installed copy after removing those copied attributes, outside the sync folder.
subprocess.run(['/usr/bin/xattr', '-cr', str(destination / 'HerdrImagePreview.app')], check=True)
subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(destination / 'HerdrImagePreview.app')], check=True)
subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(destination / 'HerdrImagePreview.app')], check=True)
# Hooks retain Codex's ordinary trust review; do not write trusted_hash entries.
codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
hooks_path = codex_home / 'hooks.json'
if hooks_path.exists():
    shutil.copy2(hooks_path, backup / 'hooks.json')
    hooks = json.loads(hooks_path.read_text())
else:
    hooks = {'hooks': {}}
command = shlex.join(['/usr/bin/python3', str(destination / 'video_hook.py')])
for event in ('SessionStart', 'UserPromptSubmit'):
    groups = hooks.setdefault('hooks', {}).setdefault(event, [])
    if not any(handler.get('command') == command for group in groups for handler in group.get('hooks', [])):
        groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 10, 'additionalContextLimit': 2500}]})
from video_attachments import write_private
write_private(hooks_path, hooks)
config = root / 'config.toml'

text = config.read_text()
if 'description = "Preview clipboard image"' not in text:
    assert 'key = "prefix+i"' not in text, 'Prefix+i is already assigned'
    text += '\n# Native image preview\n[[keys.command]]\nkey = "prefix+i"\ntype = "shell"\ncommand = "/usr/bin/open -n ' + str(destination / 'HerdrImagePreview.app') + '"\ndescription = "Preview clipboard image"\n'
    config.write_text(text)
new = binary.with_name('herdr.image-preview-new')
shutil.copy2(repo / 'target/release/herdr', new)
new.chmod(0o755)
os.replace(new, binary)
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert digest(binary) == digest(repo / 'target/release/herdr')
print('Backup:', backup)
print('Installed SHA256:', digest(binary))
subprocess.run([str(binary), 'server', 'reload-config'], check=True)
