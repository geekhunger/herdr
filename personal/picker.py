#!/usr/bin/python3
"""Keyboard picker for Herdr tabs, workspaces, or agents."""

import json
import os
import re
import select
import shutil
import subprocess
import sys
if os.name == "nt":
    import msvcrt
else:
    import termios
    import tty


def read_key(fd):
    if os.name == "nt":
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            return {"H": b"up", "P": b"down", "\x0f": b"backtab"}.get(msvcrt.getwch(), b"ignored")
        return key.encode("utf-8")
    key = os.read(fd, 1)
    if key != b"\x1b":
        return key
    sequence = key
    while len(sequence) < 32 and select.select([fd], [], [], 0.08)[0]:
        sequence += os.read(fd, 1)
        if len(sequence) == 2 and sequence[1:] not in (b"[", b"O"):
            break
        if len(sequence) >= 3 and 0x40 <= sequence[-1] <= 0x7e:
            break
    if sequence == b"\x1bOA" or re.fullmatch(rb"\x1b\[[0-9;]*A", sequence):
        return b"up"
    if sequence == b"\x1bOB" or re.fullmatch(rb"\x1b\[[0-9;]*B", sequence):
        return b"down"
    if sequence == b"\x1b[Z":
        return b"backtab"
    return b"\x1b" if sequence == b"\x1b" else b"ignored"


def render(tabs, selected):
    width, height = shutil.get_terminal_size((58, 14))
    rows = max(1, height - 4)
    start = max(0, min(selected - rows // 2, len(tabs) - rows))
    lines = [
        "↑/↓ oder Tab: navigieren · Enter: öffnen",
        "Zahl 1–9: direkt öffnen · Esc: abbrechen",
        "",
    ]
    for index in range(start, min(start + rows, len(tabs))):
        tab = tabs[index]
        label = "".join(c for c in tab["label"] if c.isprintable())
        marker = "›" if index == selected else " "
        active = " (aktuell)" if tab["focused"] else ""
        lines.append(f"{marker} {tab['number']:>2}  {label}{active}")
    lines.append(f"Auswahl {selected + 1} von {len(tabs)}")
    sys.stdout.write("\x1b[?25l\x1b[2J\x1b[H" + "\r\n".join(line[:width] for line in lines))
    sys.stdout.flush()


def load_items(binary, kind, workspace):
    def request(group, *arguments):
        result = subprocess.run(
            [binary, group, "list", *arguments],
            check=True, capture_output=True, text=True, timeout=5,
        )
        return json.loads(result.stdout)["result"]

    if kind == "agent":
        agents = request("agent")["agents"]
        workspaces = {w["workspace_id"]: w for w in request("workspace")["workspaces"]}
        tabs = {t["tab_id"]: t for t in request("tab")["tabs"]}
        agents.sort(key=lambda agent: (
            workspaces.get(agent["workspace_id"], {}).get("number", 999999),
            tabs.get(agent["tab_id"], {}).get("number", 999999),
        ))
        states = {"blocked": "wartet", "working": "arbeitet", "done": "fertig",
                  "idle": "bereit", "unknown": "unklar"}
        items = []
        for number, agent in enumerate(agents, 1):
            space = workspaces.get(agent["workspace_id"], {})
            tab = tabs.get(agent["tab_id"], {})
            state = states.get(agent.get("agent_status"), "unklar")
            name = agent.get("name") or agent.get("terminal_title_stripped") or agent["agent"]
            location = f"{space.get('number', '?')}:{space.get('label', agent['workspace_id'])}"
            label = f"{state} · {name[:18]} · {location} / {tab.get('label', agent['tab_id'])}"
            items.append({"number": number, "label": label,
                          "focused": agent.get("focused", False), "target": agent["pane_id"]})
        return items

    arguments = ("--workspace", workspace) if kind == "tab" else ()
    items = sorted(
        (item for item in request(kind, *arguments)[kind + "s"]
         if kind == "workspace" or item["workspace_id"] == workspace),
        key=lambda item: item["number"],
    )
    for item in items:
        item["target"] = item[kind + "_id"]
    return items


def main():
    kind = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1] in ("workspace", "agent") else "tab"
    title = {"workspace": "Workspace", "agent": "Agent", "tab": "Tab"}[kind]
    workspace = os.environ.get("HERDR_ACTIVE_WORKSPACE_ID")
    binary = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"
    if (kind == "tab" and not workspace) or not sys.stdin.isatty():
        letter = {"workspace": "W", "agent": "A", "tab": "T"}[kind]
        print(f"Diese Auswahl bitte mit Ctrl+F, dann {letter} in Herdr öffnen.")
        return 1

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd) if os.name != "nt" else None
    try:
        if os.name != "nt":
            tty.setcbreak(fd, termios.TCSANOW)
        tabs = load_items(binary, kind, workspace)
        if not tabs:
            print(f"Keine aktiven Einträge in der {title}-Auswahl.\nEsc oder Enter: schließen.", flush=True)
            while read_key(fd) not in (b"", b"\x1b", b"\r", b"\n", b"\x03", b"\x04"):
                pass
            return 0
        by_number = {str(tab["number"]): tab for tab in tabs if 1 <= tab["number"] <= 9}
        selected = next((i for i, tab in enumerate(tabs) if tab["focused"]), 0)
        render(tabs, selected)
        while True:
            key = read_key(fd)
            if key in (b"", b"\x1b", b"\x03", b"\x04"):
                return 0
            if key in (b"up", b"backtab", b"down", b"\t"):
                delta = -1 if key in (b"up", b"backtab") else 1
                selected = (selected + delta) % len(tabs)
                render(tabs, selected)
                continue
            tab = tabs[selected] if key in (b"\r", b"\n") else by_number.get(
                key.decode("ascii", errors="ignore")
            )
            if tab is not None:
                subprocess.run(
                    [binary, kind, "focus", tab["target"]],
                    check=True, capture_output=True, text=True, timeout=5,
                )
                return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"\n{title}-Auswahl fehlgeschlagen: {type(error).__name__}", flush=True)
        os.read(fd, 1)
        return 1
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
        if previous is not None:
            termios.tcsetattr(fd, termios.TCSANOW, previous)


if __name__ == "__main__":
    sys.exit(main())
