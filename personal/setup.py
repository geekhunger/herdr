#!/usr/bin/env python3
"""Install this fork, the portable keyboard profile and the independent scheduler."""
import argparse
import ast
import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import venv

PROFILE = Path(__file__).resolve().parent
ROOT = PROFILE.parent


def command(arguments):
    values = [str(value) for value in arguments]
    return subprocess.list2cmdline(values) if os.name == "nt" else shlex.join(values)


def render_config(python, base):
    text = (PROFILE / "config.toml.in").read_text(encoding="utf-8")
    for token, arguments in {
        "@PICKER@": [python, base / "profile/picker.py"],
        "@SCHEDULES@": [python, base / "app/main.py"],
    }.items():
        # The template already surrounds commands with TOML double quotes.
        text = text.replace(token, json.dumps(command(arguments), ensure_ascii=False)[1:-1])
    return text


def merge_codex_defaults(text):
    defaults = json.loads((PROFILE / "codex-defaults.json").read_text(encoding="utf-8"))
    match = re.search(r"(?m)^[ \t]*\[", text)
    root, rest = (text[:match.start()], text[match.start():]) if match else (text, "")
    for key, value in defaults.items():
        pattern = r"(?m)^" + re.escape(key) + r"\s*=.*$"
        replacement = key + " = " + json.dumps(value)
        root = re.sub(pattern, lambda _: replacement, root) if re.search(pattern, root) else root.rstrip() + "\n" + replacement + "\n"
    return merge_codex_keymap(root.rstrip() + "\n\n" + rest.lstrip("\n"))


def merge_codex_keymap(text):
    keys = ["ctrl-enter", "ctrl-j", "ctrl-m", "enter", "shift-enter", "alt-enter"]
    text = merge_codex_binding(text, "editor", "insert_newline", keys)
    return merge_codex_binding(text, "global", "clear_terminal", [])


def merge_codex_binding(text, context, action, keys):
    section = "tui.keymap." + context
    replacement = action + " = " + json.dumps(keys) + "\n"
    lines = text.splitlines(keepends=True)
    header = next((i for i, line in enumerate(lines)
                   if re.match(r"^\s*\[" + re.escape(section) + r"\]\s*(?:#.*)?$", line)), None)
    if header is None:
        return text.rstrip() + "\n\n[" + section + "]\n" + replacement
    end = next((i for i in range(header + 1, len(lines))
                if re.match(r"^\s*\[", lines[i])), len(lines))
    for start in range(header + 1, end):
        if not re.match(r"^\s*" + re.escape(action) + r"\s*=", lines[start]):
            continue
        # Binding values are strings or arrays. literal_eval is safe and also
        # handles multiline lists, comments and ']' inside a quoted binding.
        for stop in range(start + 1, end + 1):
            raw = "".join(lines[start:stop]).split("=", 1)[1]
            try:
                value = ast.literal_eval(raw.strip())
            except (SyntaxError, ValueError):
                continue
            if isinstance(value, str) or isinstance(value, list) and all(isinstance(v, str) for v in value):
                lines[start:stop] = [replacement]
                return "".join(lines)
            break
        raise ValueError("Vorhandene " + action + "-Bindung nicht automatisch ersetzbar; Konfiguration bleibt erhalten.")
    lines.insert(end, replacement)
    return "".join(lines)


def backup(path):
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, path.with_name(path.name + ".before-herdr-personal-" + stamp))


def install_codex_editor(python, base):
    helper = base / "profile/codex_editor.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROFILE / "codex_editor.py", helper)
    marker, end = "# >>> herdr-codex-editor >>>", "# <<< herdr-codex-editor <<<"
    if os.name == "nt":
        profiles = [Path.home() / "Documents" / directory / "Microsoft.PowerShell_profile.ps1"
                    for directory in ("PowerShell", "WindowsPowerShell")]
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        body = "function codex { & " + quote(python) + " " + quote(helper) + " launch -- @args }"
    else:
        shell = Path(os.environ.get("SHELL", "/bin/bash")).name
        profiles = [Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")]
        body = "codex() { " + command([python, helper, "launch", "--"]) + ' "$@"; }'
    for path in profiles:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        text = re.sub(re.escape(marker) + r".*?" + re.escape(end) + r"\n?", "", text, flags=re.S)
        backup(path)
        path.write_text(text.rstrip() + "\n\n" + marker + "\n" + body + "\n" + end + "\n", encoding="utf-8")


def config_path():
    if os.environ.get("HERDR_CONFIG_PATH"):
        return Path(os.environ["HERDR_CONFIG_PATH"])
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "herdr/config.toml"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()/"AppData/Roaming"))) / "herdr/config.toml"
    return Path.home() / ".config/herdr/config.toml"


def install_path(binary):
    directory = Path.home() / ".local/bin"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / binary.name
    backup(target)
    if target.is_symlink():
        target.unlink()
    if os.name == "nt":
        # Use a launcher so the binary retains access to its sibling ConPTY runtime.
        target = directory / "herdr.cmd"
        backup(target)
        target.write_text('@echo off\r\n"' + str(binary) + '" %*\r\n')
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                old = winreg.QueryValueEx(key, "Path")[0]
            except FileNotFoundError:
                old = ""
            if str(directory).casefold() not in [p.casefold() for p in old.split(";")]:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, str(directory) + ";" + old)
    else:
        target.symlink_to(binary)
        shell = Path(os.environ.get("SHELL", "/bin/bash")).name
        rc = Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")
        text = rc.read_text(encoding="utf-8") if rc.exists() else ""
        marker = "# herdr-personal PATH"
        if marker not in text:
            backup(rc)
            rc.write_text(text.rstrip() + '\n\n' + marker + '\nexport PATH="$HOME/.local/bin:$PATH"\n', encoding="utf-8")
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


def install_service(python, base):
    arguments = [str(python), str(base / "app/main.py"), "daemon"]
    if sys.platform == "darwin":
        label = "local.herrsch.herdr-schedules"
        path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        path.parent.mkdir(parents=True, exist_ok=True)
        backup(path)
        definition = {"Label": label, "ProgramArguments": arguments, "RunAtLoad": True,
                      "KeepAlive": True, "ThrottleInterval": 10, "WorkingDirectory": str(base),
                      "EnvironmentVariables": {"PATH": os.environ["PATH"], "PYTHONUNBUFFERED": "1",
                                               "PYTHONDONTWRITEBYTECODE": "1"},
                      "StandardOutPath": str(base / "daemon.stdout.log"),
                      "StandardErrorPath": str(base / "daemon.stderr.log")}
        path.write_bytes(plistlib.dumps(definition)); path.chmod(0o600)
        domain = "gui/" + str(os.getuid())
        loaded = subprocess.run(["launchctl", "print", domain + "/" + label], capture_output=True)
        if loaded.returncode:
            subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
        else:
            print("Dienst bereits aktiv. Codeupdates nach Abschluss laufender Jobs neu laden.")
    elif os.name == "nt":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        script = ("$a=New-ScheduledTaskAction -Execute " + quote(python) + " -Argument "
                  + quote(command(arguments[1:])) + " -WorkingDirectory " + quote(base) + ";"
                  "$t=New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name);"
                  "$s=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) "
                  "-RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;"
                  "Register-ScheduledTask -TaskName 'Herdr Schedules' -Action $a -Trigger $t -Settings $s -Force | Out-Null;"
                  "Start-ScheduledTask -TaskName 'Herdr Schedules'")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    else:
        path = Path.home() / ".config/systemd/user/herdr-schedules.service"
        path.parent.mkdir(parents=True, exist_ok=True); backup(path)
        argv = " ".join(json.dumps(value).replace("%", "%%") for value in arguments)
        env_path = json.dumps("PATH=" + os.environ["PATH"]).replace("%", "%%")
        path.write_text("[Unit]\nDescription=Herdr independent local schedules\n\n[Service]\n"
                        "ExecStart=" + argv + "\nEnvironment=" + env_path +
                        "\nEnvironment=PYTHONUNBUFFERED=1\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "herdr-schedules.service"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Build with installed Rust and Zig 0.16.0")
    parser.add_argument("--binary", type=Path, help="Use an already built binary")
    parser.add_argument("--no-service", action="store_true", help="Install without starting the scheduler")
    parser.add_argument("--no-terminal-profile", action="store_true", help="Keep macOS Terminal profile unchanged")
    parser.add_argument("--check", action="store_true", help="Validate the portable template without writing settings")
    args = parser.parse_args()
    if args.check:
        print(render_config(Path("/example home/python"), Path("/example home/schedules")))
        return
    if args.build:
        if not shutil.which("cargo") or not (os.environ.get("ZIG") or shutil.which("zig")):
            raise SystemExit("Für --build: Rust (cargo), Zig 0.16.0 und der native C/C++-Linker müssen installiert sein. Siehe personal/README.md.")
        if os.name == "nt":
            subprocess.run([sys.executable, str(ROOT / "scripts/package_windows_conpty.py"), "build-local"], check=True, cwd=ROOT)
        else:
            subprocess.run(["cargo", "build", "--release", "--locked"], check=True, cwd=ROOT)
    native = (args.binary or ROOT / ("target/release/herdr.exe" if os.name == "nt" else "target/release/herdr")).resolve()
    if not native.is_file():
        raise SystemExit("Zuerst personal/setup.py --build ausführen oder --binary PFAD angeben.")
    subprocess.run([str(native), "--version"], check=True)
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("Codex CLI fehlt: npm install -g @openai/codex; danach codex login.")
    os.umask(0o077)
    base = Path.home() / ".local/share/herdr-schedules"
    base.mkdir(parents=True, exist_ok=True)
    python = base / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(base / "venv")
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(PROFILE / "schedules/requirements.txt")], check=True)
    for directory in ["app", "bin", "profile"]:
        (base / directory).mkdir(exist_ok=True)
    for path in (PROFILE / "schedules").glob("*.py"):
        shutil.copy2(path, base / "app" / path.name)
    shutil.copy2(PROFILE / "picker.py", base / "profile/picker.py")
    installed = base / "bin" / native.name
    temporary = installed.with_name(installed.name + ".new")
    shutil.copy2(native, temporary); temporary.chmod(0o755); os.replace(temporary, installed)
    if os.name == "nt" and (native.parent / "conpty").is_dir():
        shutil.copytree(native.parent / "conpty", base / "bin/conpty", dirs_exist_ok=True)
    config = config_path(); config.parent.mkdir(parents=True, exist_ok=True); backup(config)
    config.write_text(render_config(python, base), encoding="utf-8")
    env = {**os.environ, "HERDR_CONFIG_PATH": str(config)}
    subprocess.run([str(installed), "config", "check"], env=env, check=True)
    codex_config = Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))) / "config.toml"
    codex_config.parent.mkdir(parents=True, exist_ok=True); backup(codex_config)
    codex_config.write_text(merge_codex_defaults(codex_config.read_text(encoding="utf-8") if codex_config.exists() else ""), encoding="utf-8")
    subprocess.run([codex, "mcp", "add", "herdr_schedules", "--", str(python), str(base / "app/main.py"), "mcp"], check=True)
    install_path(installed)
    install_codex_editor(python, base)
    if sys.platform == "darwin" and not args.no_terminal_profile:
        subprocess.run([sys.executable, str(PROFILE / "macos_terminal.py"), "--install"], check=True)
    if not args.no_service:
        install_service(python, base)
    print("Profil installiert. Neues Terminal öffnen → herdr. Ctrl+F → C: Schedules.")
    print("Codex-Anmeldung und Projektordner bleiben pro Computer separat.")


if __name__ == "__main__":
    main()
