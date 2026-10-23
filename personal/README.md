# Herrschs Herdr

Persönlicher Fork von [herdrdev/herdr](https://github.com/herdrdev/herdr), Basis **v0.9.5**. Dein Profil liegt auf dem Standardzweig **herrsch**. Der Zweig master bleibt als Upstream-Referenz erhalten.

Enthalten sind die native Schedule-Übersicht unter Agents, der eigene Scheduler, die gemeinsame MCP-/Tastaturverwaltung, das Vesper-Theme, sämtliche persönlichen Keymaps, die drei Auswahldialoge und Codex-Standardmodell `gpt-6.1-sol` mit Denkstufe `high`.

## Auf einem weiteren Computer

Benötigt werden Git, Python 3.9+, Codex CLI und ein Terminal. Für einen lokalen Herdr-Build zusätzlich Rust stable, **Zig 0.16.0** und der native C/C++-Linker (macOS: Xcode Command Line Tools; Linux: C/C++-Buildwerkzeuge; Windows: Visual Studio Build Tools mit C++ und Windows SDK). Rust und Zig werden aus ihren offiziellen Installationen bezogen. Codex CLI kann mit `npm install -g @openai/codex` installiert werden.

```sh
git clone --branch herrsch https://github.com/geekhunger/herdr.git
cd herdr
codex login
python3 personal/setup.py --build
```

Windows: `python` statt `python3`. Danach ein neues Terminal öffnen und `herdr` starten. Der Installer sichert bestehende Konfigurationen, erzeugt alle Pfade für den aktuellen Benutzer, registriert `herdr_schedules` in Codex und richtet den Benutzerdienst ein:

| System | Autostart |
|---|---|
| macOS | launchd bei Benutzeranmeldung |
| Linux mit systemd | systemd-Benutzerdienst |
| Windows | Aufgabe „Herdr Schedules“ bei Benutzeranmeldung |

`--binary PFAD` verwendet einen bereits gebauten Herdr aus diesem Fork. `--no-service` installiert ohne Autostart, etwa unter Linux ohne systemd; dann kann `~/.local/share/herdr-schedules/venv/bin/python ~/.local/share/herdr-schedules/app/main.py daemon` separat laufen. Unter Windows wird zusätzlich der von Upstream vorgesehene ConPTY-Begleitordner mitinstalliert. In GitHub Actions erzeugt der Workflow **Personal build** native Binärartefakte für macOS, Linux und Windows; diese müssen zur Architektur des Zielcomputers passen.

**Validierung:** Die vollständige Bedienung und echte Codex-Ausführung werden auf Apple Silicon macOS geprüft. Linux- und Windows-Tests sowie Builds laufen in GitHub Actions; ein erfolgreicher Build ersetzt dort keinen interaktiven Terminaltest. Die Terminal-App selbst ist separat einzurichten: Option/Alt muss die Taste an Herdr durchreichen, und Betriebssystem-Shortcuts dürfen die Kombination nicht abfangen.

## Tastaturprofil

Prefix ist **Ctrl+F**. Anschließend die Taste ohne festgehaltenen Prefix drücken.

| Folge nach Ctrl+F | Funktion |
|---|---|
| Ctrl+W | Tab schließen |
| Ctrl+T | Tab anlegen |
| Ctrl+N | Workspace anlegen |
| Ctrl+Q | Workspace schließen |
| ← / → | Voriger / nächster Tab |
| ↑ / ↓ | Voriger / nächster Workspace |
| T | Tab-Auswahl |
| W | Workspace-Auswahl |
| A | Agent-Auswahl |
| C | Schedules verwalten |
| F | Goto-Picker |
| Shift+T / Shift+W | Tab / Workspace umbenennen |
| q, ohne Ctrl | Nur Herdr-Oberfläche trennen; danach mit `herdr` wieder verbinden |

In Tab-/Workspace-/Agent-Auswahlen funktionieren ↑/↓, Tab/Shift+Tab, Enter und die Zahlen 1–9. Für den zehnten oder weitere Einträge die Pfeiltasten verwenden. Die Dialoge enthalten keine fest eingebauten Benutzerpfade. Die Tastaturübersicht und Details zum Scheduler stehen in [schedules/README.md](schedules/README.md).

## Was 1:1 wiederhergestellt wird

Theme, Keymaps, Auswahldialoge, native Sidebar, Schedule-Software, MCP-Anbindung sowie Modell und Denkstufe. Benutzerpfade werden beim Installieren auf den jeweiligen Computer angepasst. Die Konfiguration unter `personal/config.toml.in` ist die nachvollziehbare Quelle; spätere Änderungen am installierten Profil müssen bewusst hier übernommen werden.

Anmeldung, Zugangsdaten, Chats, laufende Terminalprozesse und Projektdateien sind nicht Bestandteil des öffentlichen Forks. Vorhandene Codex-Anmeldedaten und fremde MCP-Einträge werden vom Installer nicht kopiert; die lokale Codex-Konfiguration erhält nur die genannten Modellvorgaben und den eigenen Schedule-MCP-Eintrag. Projekte müssen auf dem neuen Rechner vorhanden sein. Schedule-Definitionen mit Projektpfaden und Ergebnissen bleiben lokal in `~/.local/share/herdr-schedules/schedules.sqlite3`; sie werden nicht versehentlich veröffentlicht. Zum Zeitpunkt der Einrichtung gibt es dort noch keine eigenen produktiven Jobs. ChatGPT-App-Automationen werden weder verwendet noch verändert.

Der Mac/PC muss für lokale Läufe eingeschaltet und der Benutzer angemeldet sein. Der Scheduler funktioniert unabhängig von geöffneten Herdr- oder ChatGPT-Fenstern. Neue Codex-Sitzungen laden den MCP-Server; bestehende Sitzungen gegebenenfalls neu öffnen.

## Aktualisieren und prüfen

```sh
git pull --ff-only
python3 personal/setup.py --build
```

Ein laufender Herdr-Server muss für die neue Sidebar nicht beendet werden. Nach dem Build nur die Oberfläche über Ctrl+F → q trennen und `herdr` neu starten. Bei Änderungen am Python-Dienst aktive Jobs erst abschließen lassen und anschließend nur den eigenen Schedule-Dienst neu laden. Der Installer unterbricht bestehende Jobs nicht automatisch.

```sh
python3 -m unittest discover -s personal/tests -v
~/.local/share/herdr-schedules/venv/bin/python -m unittest discover -s personal/schedules/tests -v
cargo test --release --locked --bin herdr client::shell
```

Für Upstream-Updates werden Änderungen zunächst in diesem Fork geprüft; ein unveränderter Upstream-Build enthält die persönliche Sidebar nicht. Dieser Fork ist eine eigene Konfiguration, keine offizielle Herdr-Veröffentlichung. Die ursprüngliche Apache-2.0-Lizenz bleibt erhalten.
