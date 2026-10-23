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
        put(screen, 1, 1, "↑/↓ oder Bild↑/↓: lesen · Esc: zurück", curses.A_DIM)
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
    viewer(screen, "Hinweis", text)


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
            put(screen, 1, 1, "Ctrl+S: übernehmen · Esc: zurück", curses.A_DIM)
            put(screen, 2, 1, "Enter: neue Zeile" if multiline else "Enter: übernehmen", curses.A_DIM)
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


FORM = [("name", "Name"), ("cwd", "Projektordner"), ("schedule", "Zeitplan"),
        ("timezone", "Zeitzone"), ("model", "Modell (leer = CLI-Standard)"),
        ("reasoning_effort", "Denkstufe (leer = CLI-Standard)"), ("context_mode", "Kontext: fresh / continue"),
        ("session_id", "Sitzungs-ID (optional)"), ("timeout_minutes", "Laufzeitlimit in Minuten"),
        ("sandbox", "Zugriff"), ("enabled", "Aktiv"), ("prompt", "Prompt (mehrzeilig)")]


def form(screen, store, job=None):
    initial = {"name": "", "cwd": os.environ.get("HERDR_ACTIVE_PANE_CWD", os.getcwd()),
               "schedule": {"type": "daily", "time": "09:00"}, "timezone": "Europe/Berlin",
               "model": None, "reasoning_effort": None, "context_mode": "fresh", "session_id": None,
               "timeout_minutes": 60, "sandbox": "workspace-write", "enabled": False, "prompt": ""}
    if job:
        initial.update({field: job[field] for field in FIELDS})
    values = {field: str(initial[field]) if initial[field] is not None else "" for field, _ in FORM}
    values["schedule"] = schedule_text(initial["schedule"])
    values["enabled"] = "ja" if initial["enabled"] else "nein"
    selected = 0
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        put(screen, 0, 1, "Schedule bearbeiten" if job else "Neuer Schedule", curses.A_BOLD)
        put(screen, 1, 1, "↑/↓/Tab: Feld · Enter: ändern", curses.A_DIM)
        put(screen, 2, 1, "Ctrl+S: speichern · Esc: zurück", curses.A_DIM)
        rows = max(1, height - 7)
        top = max(0, min(selected - rows + 1, len(FORM) - rows))
        for row, index in enumerate(range(top, min(top + rows, len(FORM))), 4):
            field, label = FORM[index]
            summary = values[field].replace("\n", " ↵ ")
            put(screen, row, 1, f"{label}: {summary}", curses.A_REVERSE if index == selected else 0)
        put(screen, height - 2, 1, "'Aktiv' mit Enter umschalten.", curses.A_DIM)
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
                values[field] = "nein" if values[field] == "ja" else "ja"
                continue
            if field == "context_mode":
                values[field] = "continue" if values[field] == "fresh" else "fresh"
                continue
            if field == "sandbox":
                values[field] = "read-only" if values[field] == "workspace-write" else "workspace-write"
                continue
            hint = ""
            if field == "schedule":
                hint = "every 2h | daily 09:00 | weekly mon,fri 09:00 | cron 0 9 * * 1-5 | once ISO-Datum"
            elif field == "reasoning_effort":
                hint = "low · medium · high · xhigh · max (modellabhängig)"
            answer = edit_text(screen, label, values[field], field == "prompt", hint)
            if answer is not None:
                values[field] = answer
        elif event == "\x13":
            try:
                data = dict(values)
                data["schedule"] = schedule_from_text(values["schedule"])
                data["enabled"] = values["enabled"] == "ja"
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
        put(screen, 0, 1, "Läufe: " + job["name"], curses.A_BOLD)
        put(screen, 1, 1, "↑/↓: Auswahl · Enter: Ergebnis", curses.A_DIM)
        put(screen, 2, 1, "X: Lauf stoppen · Esc: zurück", curses.A_DIM)
        selected = max(0, min(selected, len(runs) - 1))
        top = max(0, selected - max(1, height - 6) + 1)
        for row, index in enumerate(range(top, min(len(runs), top + max(1, height - 6))), 4):
            run = runs[index]
            date = zoned(run["started"] or run["requested"], job["timezone"])
            put(screen, row, 1, f"{date}  {run['status']}  {run['source']}", curses.A_REVERSE if index == selected else 0)
        if not runs:
            put(screen, 4, 1, "Noch keine Läufe.")
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
            text = (f"Status: {result['status']}\nLauf-ID: {result['id']}\n"
                    f"Fehler: {result['error'] or '-'}\nSitzung: {result['session_id'] or '-'}\n\n"
                    f"ERGEBNIS\n{result['answer'] or '(Noch keine Antwort)'}\n\n"
                    f"FEHLERPROTOKOLL\n{result['stderr']}\n\nEREIGNISSE\n{result['events']}")
            viewer(screen, "Laufergebnis", text)
        elif event in ("x", "X") and runs:
            store.cancel(runs[selected]["id"])


def details(screen, store, job):
    try:
        times = "\n".join(preview(job["schedule"], job["timezone"]))
    except Exception as error:
        times = str(error)
    text = (f"Name: {job['name']}\nID: {job['id']}\nAktiv: {job['enabled']}\n"
            f"Projekt: {job['cwd']}\nZeitplan: {schedule_text(job['schedule'])}\n"
            f"Zeitzone: {job['timezone']}\nNächster tatsächlicher Lauf: {job['next_run_at'] or '-'}\n"
            f"Modell: {job['model'] or 'CLI-Standard'}\nDenkstufe: {job['reasoning_effort'] or 'CLI-Standard'}\n"
            f"Kontext: {job['context_mode']}\nSitzung: {job['session_id'] or '-'}\n"
            f"Zugriff: {job['sandbox']}\nLaufzeitlimit: {job['timeout_minutes']} Minuten\n\n"
            f"PROMPT\n{job['prompt']}\n\nVORSCHAU DES ZEITPLANS\n{times}")
    viewer(screen, "Schedule-Details", text)


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
        title = "Schedules · " + ("Dienst läuft" if health["healthy"] else "DIENST NICHT ERREICHBAR")
        if archived:
            title += " · Archiv"
        put(screen, 0, 1, title, curses.A_BOLD)
        put(screen, 1, 1, "N: neu · E: ändern · Enter: Details", curses.A_DIM)
        put(screen, 2, 1, "Leertaste: Pause · R: starten · L: Läufe", curses.A_DIM)
        put(screen, 3, 1, "D: archivieren · A: Archiv · U: zurück", curses.A_DIM)
        put(screen, 4, 1, "↑/↓/Tab: Auswahl · Esc: zurück · ?: Hilfe", curses.A_DIM)
        selected = max(0, min(selected, len(jobs) - 1))
        rows = max(1, (height - 9) // 3)
        top = max(0, min(selected - rows + 1, len(jobs) - rows))
        for offset, index in enumerate(range(top, min(len(jobs), top + rows))):
            row = 6 + 3 * offset
            job = jobs[index]
            active = "AKTIV" if job["enabled"] else "PAUSE"
            last = job["last_run"]["status"] if job["last_run"] else "noch kein Lauf"
            due = (job["next_run_at"] or "-").replace("T", " ")
            put(screen, row, 1, f"{active:5}  {job['name']}",
                curses.A_REVERSE if index == selected else 0)
            put(screen, row + 1, 1, f"  {schedule_text(job['schedule'])} · {last}", curses.A_DIM)
            put(screen, row + 2, 1, f"  Nächster Lauf: {due}", curses.A_DIM)
        if not jobs:
            put(screen, 6, 1, "Keine Schedules im Archiv." if archived else "Noch keine eigenen Schedules.")
            if not archived:
                put(screen, 7, 1, "Mit N anlegen oder per MCP erstellen.")
        put(screen, height - 2, 1, "Mac an · Hintergrunddienst erforderlich", curses.A_DIM)
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
                viewer(screen, "Bedienung", "Ctrl+F → C öffnet diese Verwaltung.\n\n"
                       "N: neuer Schedule. Pfeile/Tab wählen ein Feld. Enter bearbeitet es.\n"
                       "Prompt: mehrzeilig; Ctrl+S übernimmt den Text. Ctrl+S im Formular speichert den Job.\n"
                       "Zeitplan: every 2h; daily 09:15; weekly mon,wed,fri 09:00; cron 0 9 * * 1-5; once ISO-Datum.\n"
                       "Neue Jobs sind manuell zunächst pausiert; Aktiv im Formular oder Leertaste schaltet sie ein.\n"
                       "fresh startet jeden Lauf neu. continue setzt eine eigene Codex-Sitzung fort.\n"
                       "Leer gelassenes Modell und Denkstufe verwenden die Codex-CLI-Standards.\n"
                       "R startet einmal manuell; L zeigt echte Antworten und Fehler. X in der Laufansicht stoppt einen Lauf.\n"
                       "Verpasste Wiederholungen werden zu einem nachgeholten Lauf zusammengefasst.\n"
                       "Ein Job läuft nie mehrfach gleichzeitig; insgesamt arbeitet ein Job nach dem anderen.\n"
                       "D verschiebt ins Archiv. A zeigt das Archiv; U stellt pausiert wieder her.\n"
                       "Der launchd-Dienst muss laufen. Mac muss eingeschaltet, Benutzer angemeldet und verfügbar sein.")
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
                    message(screen, "Lauf bereits vorgemerkt/aktiv." if queued["already_pending"] else "Lauf in Warteschlange. Status und Ergebnis findest du mit L.\nDie Einreihung ist noch kein erfolgreiches Ergebnis.")
                elif event in ("l", "L"):
                    run_history(screen, store, job)
                elif event in ("d", "D") and not archived:
                    answer = edit_text(screen, "Archivieren? JA eingeben", "")
                    if answer and answer.upper() == "JA":
                        store.archive(job["id"])
                elif event in ("u", "U") and archived:
                    store.archive(job["id"], restore=True)
        except Exception as error:
            message(screen, str(error))


def start(store):
    curses.wrapper(ui, store)
