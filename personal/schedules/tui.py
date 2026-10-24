"""Keyboard-only schedule manager. Every edit uses the same Store as MCP."""
import curses
import datetime as dt
import os
from pathlib import Path
import textwrap

from core import FIELDS, preview, schedule_from_text, schedule_text, zoned


def put(screen, y, x, text, style=0):
    height, width = screen.getmaxyx()
    if y < 0 or y >= height or x >= width - 1:
        return
    text = "".join(c if c.isprintable() else " " for c in str(text))
    try:
        screen.addnstr(y, x, text, max(0, width - x - 1), style)
    except curses.error:
        pass


def key(screen):
    try:
        return screen.get_wch()
    except curses.error:
        return None


def is_enter(value):
    return value in ("\n", "\r", curses.KEY_ENTER)


def viewer(screen, title, text):
    offset = 0
    while True:
        height, width = screen.getmaxyx()
        lines = []
        for paragraph in str(text).splitlines():
            lines += textwrap.wrap(paragraph, max(10, width - 4), replace_whitespace=False) or [""]
        screen.erase()
        put(screen, 0, 1, title, curses.A_BOLD)
        put(screen, 1, 1, "↑/↓ or Page Up/Down: scroll · Esc: back", curses.A_DIM)
        offset = min(max(0, offset), max(0, len(lines) - max(1, height - 4)))
        for row, line in enumerate(lines[offset:offset + max(1, height - 4)], 3):
            put(screen, row, 1, line)
        screen.refresh()
        value = key(screen)
        if value in ("\x1b", "\x03"):
            return
        if value in (curses.KEY_DOWN, "j"):
            offset += 1
        elif value in (curses.KEY_UP, "k"):
            offset -= 1
        elif value == curses.KEY_NPAGE:
            offset += max(1, height - 5)
        elif value == curses.KEY_PPAGE:
            offset -= max(1, height - 5)


def message(screen, text):
    viewer(screen, "Notice", text)


def edit_text(screen, title, text, multiline=False, hint=""):
    value, cursor = str(text), len(str(text))
    screen.timeout(-1)
    curses.curs_set(1)
    try:
        while True:
            height, width = screen.getmaxyx()
            columns = max(10, width - 4)
            positions, lines = [], [""]
            row = column = 0
            for char in value:
                positions.append((row, column))
                if char == "\n":
                    row, column = row + 1, 0
                    lines.append("")
                else:
                    lines[-1] += char
                    column += 1
                    if column >= columns:
                        row, column = row + 1, 0
                        lines.append("")
            positions.append((row, column))
            cursor_row, cursor_column = positions[cursor]
            available = max(1, height - 6)
            top = max(0, cursor_row - available + 1)
            screen.erase()
            put(screen, 0, 1, title, curses.A_BOLD)
            put(screen, 1, 1, "Ctrl+S: apply · Esc: back", curses.A_DIM)
            put(screen, 2, 1, "Enter: new line" if multiline else "Enter: apply", curses.A_DIM)
            put(screen, 3, 1, hint, curses.A_DIM)
            for display_row, line in enumerate(lines[top:top + available], 4):
                put(screen, display_row, 2, line)
            screen.move(min(height - 2, 4 + cursor_row - top), min(width - 2, 2 + cursor_column))
            screen.refresh()
            event = key(screen)
            if event == "\x03":
                return None
            if event == "\x1b":
                screen.timeout(90)
                following = key(screen)
                screen.timeout(-1)
                if following is None:
                    return None
                if following in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    boundary = cursor
                    while boundary and value[boundary - 1].isspace():
                        boundary -= 1
                    while boundary and not value[boundary - 1].isspace():
                        boundary -= 1
                    value, cursor = value[:boundary] + value[cursor:], boundary
                continue
            if event == "\x13" or (is_enter(event) and not multiline):
                return value
            if is_enter(event):
                value, cursor = value[:cursor] + "\n" + value[cursor:], cursor + 1
            elif event == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif event == curses.KEY_RIGHT:
                cursor = min(len(value), cursor + 1)
            elif event in (curses.KEY_HOME, "\x01"):
                cursor = value.rfind("\n", 0, cursor) + 1
            elif event in (curses.KEY_END, "\x05"):
                end = value.find("\n", cursor)
                cursor = len(value) if end < 0 else end
            elif event in (curses.KEY_UP, curses.KEY_DOWN):
                target = cursor_row + (-1 if event == curses.KEY_UP else 1)
                candidates = [i for i, pos in enumerate(positions) if pos[0] == target]
                if candidates:
                    cursor = min(candidates, key=lambda i: abs(positions[i][1] - cursor_column))
            elif event in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                if cursor:
                    value, cursor = value[:cursor - 1] + value[cursor:], cursor - 1
            elif event in (curses.KEY_DC, "\x04"):
                value = value[:cursor] + value[cursor + 1:]
            elif event == "\x17":
                boundary = cursor
                while boundary and value[boundary - 1].isspace():
                    boundary -= 1
                while boundary and not value[boundary - 1].isspace():
                    boundary -= 1
                value, cursor = value[:boundary] + value[cursor:], boundary
            elif event == "\x15":
                boundary = value.rfind("\n", 0, cursor) + 1
                value, cursor = value[:boundary] + value[cursor:], boundary
            elif event == "\x0b":
                boundary = value.find("\n", cursor)
                value = value[:cursor] + (value[boundary:] if boundary >= 0 else "")
            elif isinstance(event, str) and event.isprintable():
                value, cursor = value[:cursor] + event + value[cursor:], cursor + len(event)
    finally:
        curses.curs_set(0)
        screen.timeout(1000)


FORM = [("name", "Name"), ("cwd", "Project directory"), ("schedule", "Schedule"),
        ("timezone", "Timezone"), ("model", "Model (empty = CLI default)"),
        ("reasoning_effort", "Reasoning effort (empty = CLI default)"), ("context_mode", "Context: fresh / continue"),
        ("session_id", "Session ID (optional)"), ("timeout_minutes", "Timeout in minutes"),
        ("sandbox", "Access"), ("enabled", "Enabled"), ("prompt", "Prompt (multiline)")]


def form(screen, store, job=None):
    initial = {"name": "", "cwd": os.environ.get("HERDR_ACTIVE_PANE_CWD", os.getcwd()),
               "schedule": {"type": "daily", "time": "09:00"}, "timezone": "Europe/Berlin",
               "model": None, "reasoning_effort": None, "context_mode": "fresh", "session_id": None,
               "timeout_minutes": 60, "sandbox": "workspace-write", "enabled": False, "prompt": ""}
    if job:
        initial.update({field: job[field] for field in FIELDS})
    values = {field: str(initial[field]) if initial[field] is not None else "" for field, _ in FORM}
    values["schedule"] = schedule_text(initial["schedule"])
    values["enabled"] = "yes" if initial["enabled"] else "no"
    selected = 0
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        put(screen, 0, 1, "Edit schedule" if job else "New schedule", curses.A_BOLD)
        put(screen, 1, 1, "↑/↓/Tab: field · Enter: edit", curses.A_DIM)
        put(screen, 2, 1, "Ctrl+S: save · Esc: back", curses.A_DIM)
        rows = max(1, height - 7)
        top = max(0, min(selected - rows + 1, len(FORM) - rows))
        for row, index in enumerate(range(top, min(top + rows, len(FORM))), 4):
            field, label = FORM[index]
            summary = values[field].replace("\n", " ↵ ")
            put(screen, row, 1, f"{label}: {summary}", curses.A_REVERSE if index == selected else 0)
        put(screen, height - 2, 1, "Press Enter to toggle 'Enabled'.", curses.A_DIM)
        screen.refresh()
        event = key(screen)
        if event in ("\x1b", "\x03"):
            return
        if event in (curses.KEY_DOWN, "\t"):
            selected = (selected + 1) % len(FORM)
        elif event in (curses.KEY_UP, curses.KEY_BTAB):
            selected = (selected - 1) % len(FORM)
        elif is_enter(event):
            field, label = FORM[selected]
            if field == "enabled":
                values[field] = "no" if values[field] == "yes" else "yes"
                continue
            if field == "context_mode":
                values[field] = "continue" if values[field] == "fresh" else "fresh"
                continue
            if field == "sandbox":
                values[field] = "read-only" if values[field] == "workspace-write" else "workspace-write"
                continue
            hint = ""
            if field == "schedule":
                hint = "every 2h | daily 09:00 | weekly mon,fri 09:00 | cron 0 9 * * 1-5 | once ISO datetime"
            elif field == "reasoning_effort":
                hint = "low · medium · high · xhigh · max (depending on model)"
            answer = edit_text(screen, label, values[field], field == "prompt", hint)
            if answer is not None:
                values[field] = answer
        elif event == "\x13":
            try:
                data = dict(values)
                data["schedule"] = schedule_from_text(values["schedule"])
                data["enabled"] = values["enabled"] == "yes"
                data["timeout_minutes"] = float(values["timeout_minutes"])
                for field in ("model", "reasoning_effort", "session_id"):
                    data[field] = values[field] or None
                if job:
                    store.update(job["id"], data, expected_revision=job["revision"])
                else:
                    store.create(data)
                return
            except Exception as error:
                message(screen, str(error))


def run_history(screen, store, job):
    selected = 0
    while True:
        runs = store.runs(job["id"])
        screen.erase()
        height, width = screen.getmaxyx()
        put(screen, 0, 1, "Runs: " + job["name"], curses.A_BOLD)
        put(screen, 1, 1, "↑/↓: select · Enter: result", curses.A_DIM)
        put(screen, 2, 1, "X: stop run · Esc: back", curses.A_DIM)
        selected = max(0, min(selected, len(runs) - 1))
        top = max(0, selected - max(1, height - 6) + 1)
        for row, index in enumerate(range(top, min(len(runs), top + max(1, height - 6))), 4):
            run = runs[index]
            date = zoned(run["started"] or run["requested"], job["timezone"])
            put(screen, row, 1, f"{date}  {run['status']}  {run['source']}", curses.A_REVERSE if index == selected else 0)
        screen.refresh()
        event = key(screen)
        if event in ("\x1b", "\x03"):
            return
        if event in (curses.KEY_DOWN, "\t") and runs:
            selected = (selected + 1) % len(runs)
        elif event in (curses.KEY_UP, curses.KEY_BTAB) and runs:
            selected = (selected - 1) % len(runs)
        elif is_enter(event) and runs:
            result = store.result(runs[selected]["id"], 60000)
            text = (f"Status: {result['status']}\nRun ID: {result['id']}\n"
                    f"Error: {result['error'] or '-'}\nSession: {result['session_id'] or '-'}\n\n"
                    f"RESULT\n{result['answer'] or '(No answer yet)'}\n\n"
                    f"ERROR LOG\n{result['stderr']}\n\nEVENTS\n{result['events']}")
            viewer(screen, "Run result", text)
        elif event in ("x", "X") and runs:
            store.cancel(runs[selected]["id"])


def details(screen, store, job):
    try:
        times = "\n".join(preview(job["schedule"], job["timezone"]))
    except Exception as error:
        times = str(error)
    text = (f"Name: {job['name']}\nID: {job['id']}\nEnabled: {job['enabled']}\n"
            f"Project: {job['cwd']}\nSchedule: {schedule_text(job['schedule'])}\n"
            f"Timezone: {job['timezone']}\nNext scheduled run: {job['next_run_at'] or '-'}\n"
            f"Model: {job['model'] or 'CLI default'}\nReasoning effort: {job['reasoning_effort'] or 'CLI default'}\n"
            f"Context: {job['context_mode']}\nSession: {job['session_id'] or '-'}\n"
            f"Access: {job['sandbox']}\nTimeout: {job['timeout_minutes']} minutes\n\n"
            f"PROMPT\n{job['prompt']}\n\nSCHEDULE PREVIEW\n{times}")
    viewer(screen, "Schedule details", text)


def ui(screen, store):
    curses.raw()  # Ctrl+S must reach the form, not stop terminal output (XOFF).
    curses.curs_set(0)
    curses.set_escdelay(40)
    screen.keypad(True)
    screen.timeout(1000)
    selected, archived = 0, False
    while True:
        jobs = store.list(include_deleted=archived)
        if archived:
            jobs = [job for job in jobs if job["deleted"]]
        health = store.health()
        height, width = screen.getmaxyx()
        screen.erase()
        title = "Schedules · " + ("Service running" if health["healthy"] else "SERVICE UNAVAILABLE")
        if archived:
            title += " · Archive"
        put(screen, 0, 1, title, curses.A_BOLD)
        put(screen, 1, 1, "N: new · E: edit · Enter: details", curses.A_DIM)
        put(screen, 2, 1, "Space: pause/resume · R: run · L: history", curses.A_DIM)
        put(screen, 3, 1, "D: archive · A: archive view · U: restore", curses.A_DIM)
        put(screen, 4, 1, "↑/↓/Tab: select · Esc: back · ?: help", curses.A_DIM)
        selected = max(0, min(selected, len(jobs) - 1))
        rows = max(1, (height - 9) // 3)
        top = max(0, min(selected - rows + 1, len(jobs) - rows))
        for offset, index in enumerate(range(top, min(len(jobs), top + rows))):
            row = 6 + 3 * offset
            job = jobs[index]
            active = "ON" if job["enabled"] else "PAUSED"
            last = job["last_run"]["status"] if job["last_run"] else "no runs yet"
            due = (job["next_run_at"] or "-").replace("T", " ")
            put(screen, row, 1, f"{active:5}  {job['name']}",
                curses.A_REVERSE if index == selected else 0)
            put(screen, row + 1, 1, f"  {schedule_text(job['schedule'])} · {last}", curses.A_DIM)
            put(screen, row + 2, 1, f"  Next run: {due}", curses.A_DIM)
        put(screen, height - 2, 1, "Computer must be on · Background service required", curses.A_DIM)
        screen.refresh()
        event = key(screen)
        try:
            if event in ("\x1b", "\x03"):
                return
            if event in (curses.KEY_DOWN, "\t") and jobs:
                selected = (selected + 1) % len(jobs)
            elif event in (curses.KEY_UP, curses.KEY_BTAB) and jobs:
                selected = (selected - 1) % len(jobs)
            elif event in ("n", "N"):
                form(screen, store)
            elif event in ("a", "A"):
                archived, selected = not archived, 0
            elif event == "?":
                viewer(screen, "Help", "Ctrl+F → C opens this manager.\n\n"
                       "N: new schedule. Arrows/Tab select a field. Enter edits it.\n"
                       "Prompt: multiline; Ctrl+S applies the text. Ctrl+S in the form saves the job.\n"
                       "Schedule: every 2h; daily 09:15; weekly mon,wed,fri 09:00; cron 0 9 * * 1-5; once ISO datetime.\n"
                       "New jobs start paused; use Enabled in the form or Space to enable them.\n"
                       "fresh starts a new conversation each run. continue resumes the job’s Codex session.\n"
                       "Empty Model and Reasoning effort use the Codex CLI defaults.\n"
                       "R runs once manually; L shows answers and errors. X in run history stops a run.\n"
                       "Missed repetitions are combined into one catch-up run.\n"
                       "A job never overlaps itself; jobs run one at a time.\n"
                       "D archives. A shows the archive; U restores a job paused.\n"
                       "The background service must be running. The computer must be on and the user session available.")
            elif jobs:
                job = jobs[selected]
                if is_enter(event):
                    details(screen, store, job)
                elif event in ("e", "E") and not archived:
                    form(screen, store, job)
                elif event == " " and not archived:
                    store.update(job["id"], {"enabled": not job["enabled"]}, expected_revision=job["revision"])
                elif event in ("r", "R") and not archived:
                    queued = store.enqueue(job["id"])
                    message(screen, "Run already queued/running." if queued["already_pending"] else "Run queued. Press L to view status and results.\nA queued run is not a successful result.")
                elif event in ("l", "L"):
                    run_history(screen, store, job)
                elif event in ("d", "D") and not archived:
                    answer = edit_text(screen, "Archive? Type YES", "")
                    if answer and answer.upper() == "YES":
                        store.archive(job["id"])
                elif event in ("u", "U") and archived:
                    store.archive(job["id"], restore=True)
        except Exception as error:
            message(screen, str(error))


def start(store):
    curses.wrapper(ui, store)
