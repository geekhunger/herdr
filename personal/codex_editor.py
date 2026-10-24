#!/usr/bin/env python3
"""One-shot draft clearing through Codex's own external-editor interface."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid


def state_root():
    if os.environ.get("XDG_CONFIG_HOME"):
        base = Path(os.environ["XDG_CONFIG_HOME"])
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
    else:
        base = Path.home() / ".config"
    return base / "herdr" / "draft-editor"


def identity(socket, pane):
    return hashlib.sha256((socket + "\0" + pane).encode("utf-8")).hexdigest()


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex)
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.chmod(0o600)
    os.replace(temp, path)


def launch(arguments):
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    real = shutil.which("codex")
    if not real:
        raise SystemExit("Codex CLI not found in PATH.")
    pane, socket = os.environ.get("HERDR_PANE_ID"), os.environ.get("HERDR_SOCKET_PATH")
    if pane and socket:
        token = uuid.uuid4().hex
        key = identity(socket, pane)
        write_private(state_root() / (key + ".ready"), {"pid": os.getpid(), "token": token})
        os.environ["HERDR_DRAFT_EDITOR_TOKEN"] = token
        for name in ("VISUAL", "EDITOR"):
            os.environ["HERDR_ORIGINAL_" + name] = os.environ.get(name, "")
        command = [sys.executable, str(Path(__file__).resolve()), "edit"]
        editor = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
        os.environ["VISUAL"] = os.environ["EDITOR"] = editor
    if os.name == "nt":
        raise SystemExit(subprocess.call([real] + arguments))
    os.execv(real, [real] + arguments)


def clear_requested_draft(path, root=None, now=None):
    pane, socket = os.environ.get("HERDR_PANE_ID"), os.environ.get("HERDR_SOCKET_PATH")
    token = os.environ.get("HERDR_DRAFT_EDITOR_TOKEN")
    if not pane or not socket or not token:
        return False
    root = root or state_root()
    request = root / (identity(socket, pane) + ".request")
    try:
        if request.stat().st_size > 4096:
            return False
        data = json.loads(request.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("created_ms"), (int, float)):
        return False
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    # Only Codex's temporary draft, never arbitrary files passed to $EDITOR.
    if path.is_symlink() or path.resolve().parent != (codex_home / "editor").resolve():
        return False
    if not path.name.startswith(".tmp") or path.suffix != ".md":
        return False
    age = (time.time() if now is None else now) * 1000 - data.get("created_ms", 0)
    if data.get("token") != token or not 0 <= age <= 10000:
        return False
    request.unlink()
    path.write_text("", encoding="utf-8")
    return True


def edit(arguments):
    if len(arguments) == 1 and clear_requested_draft(Path(arguments[0])):
        return
    # Ordinary Ctrl+G retains the user's editor. Agent tools cannot consume a
    # clear request by opening a project file or a Git commit-message file.
    original = os.environ.get("HERDR_ORIGINAL_VISUAL") or os.environ.get("HERDR_ORIGINAL_EDITOR")
    if os.name == "nt":
        raise SystemExit(subprocess.call((original or "notepad.exe") + " " + subprocess.list2cmdline(arguments)))
    command = shlex.split(original) if original else (["notepad.exe"] if os.name == "nt" else ["vi"])
    os.execvp(command[0], command + arguments)


if __name__ == "__main__":
    if sys.argv[1:2] == ["launch"]:
        launch(sys.argv[2:])
    elif sys.argv[1:2] == ["edit"]:
        edit(sys.argv[2:])
    else:
        raise SystemExit("usage: codex_editor.py launch -- [Codex arguments] | edit DRAFT")
