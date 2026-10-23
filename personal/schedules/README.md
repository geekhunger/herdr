# Herdr Schedules

Eigene lokale Schedule-Verwaltung für macOS. Tastaturdialog und MCP greifen auf dieselbe SQLite-Datenbank zu. Ein Benutzer-LaunchAgent startet Codex CLI direkt mit `--no-daemon`; die ChatGPT-App und ihr Scheduler werden nicht verwendet.

Der vollständige persönliche Fork einschließlich portabler Keymaps und Einrichtung für weitere Rechner liegt unter [geekhunger/herdr, Zweig herrsch](https://github.com/geekhunger/herdr/tree/herrsch). Die Anleitung dafür ist [personal/README.md](https://github.com/geekhunger/herdr/blob/herrsch/personal/README.md).

## Tastatur

`Ctrl+F`, loslassen, `C` öffnet die Verwaltung.

| Taste | Aktion |
|---|---|
| ↑ / ↓, Tab / Shift+Tab | Job oder Formularfeld auswählen |
| N | Neuen Job anlegen |
| E | Ausgewählten Job bearbeiten |
| Enter | Details öffnen; im Formular ein Feld bearbeiten |
| Ctrl+S | Feldtext übernehmen; im Formular den gesamten Job speichern |
| Leertaste | Job aktivieren oder pausieren |
| R | Einmaligen Lauf in die Warteschlange stellen |
| L | Läufe, Ergebnisse und Fehlerprotokolle ansehen |
| X in der Laufansicht | Lauf abbrechen |
| D | Wiederherstellbar archivieren; mit JA bestätigen |
| A | Zwischen Jobs und Archiv wechseln |
| U im Archiv | Pausiert wiederherstellen |
| Esc | Zurück beziehungsweise schließen |
| ? | Hilfe |

Neue Jobs sind im manuellen Formular zunächst pausiert. Das Feld **Aktiv** wird mit Enter umgeschaltet. Im mehrzeiligen Prompt erzeugt Enter eine neue Zeile; Ctrl+S übernimmt den Text. Danach speichert Ctrl+S im Formular den Job. Ctrl+W und Option+Backspace löschen das vorige Wort.

## Dauerhafte Übersicht unter Agents

Die lokale Herdr-Version teilt die ausgeklappte linke Sidebar in Spaces, Agents und Schedules. Schedules zeigt die Anzahl aktiver Jobs, Namen, aktiv/pausiert, den nächsten Termin und den letzten Laufstatus. Laufende und fehlgeschlagene Jobs stehen oben. Die Übersicht aktualisiert sich ungefähr alle zwei Sekunden. Ein fehlender Dienst wird nach spätestens 20 Sekunden angezeigt. Bei mehr Jobs als Platz erscheint „+N“; Ctrl+F → C öffnet die vollständige Liste.

Die Übersicht verwendet einen atomar geschriebenen, begrenzten JSON-Snapshot des eigenen Schedule-Dienstes. Der Herdr-Client liest ihn einmal pro Sekunde außerhalb der Zeichenroutine. Der Server und seine Protokolle bleiben unverändert. Bei eingeklappter Sidebar oder weniger als 15 Sidebar-Zeilen bleibt die vollständige Verwaltung über Ctrl+F → C erreichbar.

**Einmalig den Herdr-Client neu öffnen:** Ctrl+F → q (kleines q, ohne Ctrl) trennt nur die Oberfläche. Danach im Terminal `herdr` eingeben. Workspaces, Tabs und laufende Agenten bleiben auf dem bestehenden Server erhalten. Ctrl+F → Ctrl+Q ist weiterhin dein Befehl zum Schließen eines Workspace.

Die Sidebar ist direkt im Quellcode dieses Forks enthalten. Der portable Installer unter `personal/setup.py` baut Herdr oder verwendet `--binary PFAD`, kopiert das Profil in das Benutzerverzeichnis und richtet einen Starter unter `~/.local/bin` ein. Das offizielle Homebrew-Programm wird dadurch nicht überschrieben. Native Pakete müssen zur Architektur des Zielrechners passen.

## Zeitpläne

Im manuellen Zeitplanfeld:

```text
every 2h
daily 09:15
weekly mon,wed,fri 09:00
cron 0 9 * * 1-5
once 2026-12-01T10:00:00+01:00
```

Standardzeitzone: `Europe/Berlin`. Wochentage sind auch als `mo,di,mi,do,fr,sa,so` möglich. Intervallangaben unterstützen Minuten (`m`), Stunden (`h`) und Tage (`d`). Cron verwendet fünf Felder. Zeitvorschau und tatsächlicher nächster Termin sind in den Details sichtbar.

Lokale Uhrzeiten folgen der Zeitzone. Nicht existierende Uhrzeiten während der Zeitumstellung werden übersprungen; doppelte Uhrzeiten laufen einmal, beim ersten Auftreten. Verpasste Wiederholungen werden beim Wiederanlaufen zu einem Lauf zusammengefasst. Ein einmaliger Termin wird nach Einreihung automatisch deaktiviert.

## Per Chat / MCP

Der lokale stdio-Server heißt **herdr_schedules**. Neue Codex-Sitzungen laden ihn aus der gemeinsamen Codex-MCP-Konfiguration. Bereits offene Sitzungen gegebenenfalls neu öffnen; `/mcp` zeigt die Verbindung.

Beispiele für Aufträge an einen Agenten:

- „Erstelle über herdr_schedules für diesen Projektordner jeden Montag um 9 Uhr einen Job: Prüfe neue Fehler und berichte nur Änderungen.“
- „Zeige meine lokalen Schedules und verschiebe den Bericht auf 18 Uhr.“
- „Ändere nur den Prompt dieses Jobs und zeige mir danach den gespeicherten Stand.“
- „Pausiere alle Schedules für dieses Projekt.“

Werkzeuge: `schedules_list`, `schedules_get`, `schedules_preview`, `schedules_create`, `schedules_update`, `schedules_pause`, `schedules_resume`, `schedules_delete`, `schedules_restore`, `schedules_run_now`, `schedules_runs`, `schedules_run_result`, `schedules_cancel_run`, `schedules_health`.

Zeitpläne als MCP-Objekte:

```json
{"type":"daily","time":"09:15"}
{"type":"weekly","days":[0,2,4],"time":"09:00"}
{"type":"interval","minutes":120}
{"type":"cron","expression":"0 9 * * 1-5"}
{"type":"once","at":"2026-12-01T10:00:00+01:00"}
```

Bei Wochentagen ist 0 Montag, 6 Sonntag. MCP-Erstellung aktiviert einen Job standardmäßig; zum Entwerfen `enabled: false` angeben. Bei Updates schützt `expected_revision` vor dem Überschreiben gleichzeitiger Änderungen. Ein optionaler `request_key` verhindert doppelte Jobs bei wiederholten Erstellungsaufrufen. Der Server liest und ändert keine ChatGPT-Automationen.

## Ausführung und Kontext

- `fresh`: Jeder Lauf startet eine neue Codex-Sitzung.
- `continue`: Der erste Lauf erzeugt eine eigene Sitzung; weitere Läufe setzen sie fort. Optional kann eine vorhandene Codex-Sitzungs-ID angegeben werden.
- Projektdateien und die dortige `AGENTS.md` kommen über den festgelegten Projektordner hinzu. ChatGPT-Projektuploads und App-spezifische Werkzeuge werden nicht automatisch übertragen.
- Modell und Denkstufe sind pro Job einstellbar. Leer bedeutet: aktuelle Codex-CLI-Standardeinstellung.
- Zugriff: `read-only` oder `workspace-write`. Unbeaufsichtigte Läufe können keine Rückfragen zur Freigabe beantworten. Fehler und Blockaden müssen aus dem Ergebnis/Protokoll beurteilt werden.
- Standardlimit: 60 Minuten, einstellbar von 1 bis 1440. Insgesamt läuft ein Job gleichzeitig. Derselbe Job wird nicht parallel oder doppelt eingereiht.
- Pausieren verhindert zukünftige automatische Starts; es beendet keinen bereits laufenden Auftrag. Ein Lauf kann separat mit X beziehungsweise MCP abgebrochen werden.
- Ein Job in der Warteschlange ist noch kein erfolgreicher Lauf. Ergebnisse enthalten Exit-Code, Antwort, Ereignisprotokoll und Fehlerausgabe.
- Der Mac muss verfügbar und der Benutzer angemeldet sein. Der launchd-Dienst benötigt weder ein offenes Herdr-Fenster noch die ChatGPT-App. Codex muss weiterhin angemeldet sein und benötigt für die Modellausführung Netzwerk und Kontingent.

## Speicher und Dienst

Installation: `~/.local/share/herdr-schedules/`

- `app/`: Programmcode
- `venv/`: eigene Python-Laufzeit mit Zeitplanbibliothek
- `schedules.sqlite3`: gemeinsame Datenbank
- `sidebar.json`: ausschließlich die kompakte Schedule-Übersicht
- `runs/<Lauf-ID>/`: Prompt-Snapshot, Antwort, Ereignis- und Fehlerprotokoll
- `daemon.stdout.log`, `daemon.stderr.log`: Dienstprotokolle

macOS-LaunchAgent: `~/Library/LaunchAgents/local.herrsch.herdr-schedules.plist`. Linux verwendet einen systemd-Benutzerdienst, Windows die Aufgabe „Herdr Schedules“ bei Benutzeranmeldung.

Der Dienst läuft als angemeldeter Benutzer; er wird bei Anmeldung gestartet und nach einem Absturz neu gestartet. Eine SQLite-Transaktion verhindert konkurrierende Einreihungen. Prozesssperren erkennen abgebrochene Worker; unklare abgebrochene Läufe werden nicht automatisch noch einmal ausgeführt.

Die vorhandenen ChatGPT-Jobs bleiben in ihrer bisherigen Verwaltung. Es werden keine vorhandenen Automationen importiert oder ersetzt.

## Installation und Prüfung

```sh
python3 personal/setup.py --build
```

Der portable Installer sichert Konfigurationen, kopiert den Programmcode, registriert den MCP-Server und richtet den jeweiligen Benutzerdienst ein. Wenn der Dienst schon läuft, wird er nicht unterbrochen. Bei späteren Codeupdates muss der Dienst nach Abschluss aktiver Läufe gezielt neu gestartet werden.

Tests in diesem Quellordner:

```sh
~/.local/share/herdr-schedules/venv/bin/python -m unittest discover -s tests -v
```

Isolierte Testdaten sind über `HERDR_SCHEDULES_HOME` möglich. `HERDR_SCHEDULES_CODEX` ist ausschließlich für einen expliziten Test-Runner vorgesehen. Die Produktivinstallation findet die eigenständige Codex CLI im PATH; auf diesem Mac unter `/opt/homebrew/bin/codex`.
