# Herdr – persönlicher Fork

Dein Herdr-Profil auf dem Standardzweig **herrsch**, auf Basis von Herdr 0.9.5.

- **Ctrl+F** als Prefix, persönliche Keymaps und Vesper-Theme.
- Tab-, Workspace- und Agent-Auswahl mit Pfeiltasten und Enter.
- Eigene **Schedules unter Agents**, Verwaltung über **Ctrl+F → C** und per MCP in natürlicher Sprache.
- **Text markieren → Rechtsklick → Reply** übernimmt ein Zitat in die Codex-Eingabe. Die eigene Antwort darunter schreiben und selbst absenden.
- Codex-Standardmodell **gpt-6.1-sol**, Denkstufe **high**.

## Auf einem weiteren Rechner einrichten

```sh
git clone --branch herrsch https://github.com/geekhunger/herdr.git
cd herdr
codex login
python3 personal/setup.py --build
```

Unter Windows `python` statt `python3` verwenden. Die vollständigen Voraussetzungen, Betriebssysteme, fertigen Build-Artefakte und Tastenkürzel stehen in der [persönlichen Installationsanleitung](../personal/README.md). Der Installer erzeugt passende Benutzerpfade und sichert bestehende Konfigurationen. Anmeldung und Projektdateien werden auf jedem Computer separat eingerichtet.

Die Schedules laufen unabhängig von der ChatGPT-App über einen eigenen Benutzerdienst. Zugangsdaten, Chats und lokale Schedule-Datenbanken gehören nicht zum öffentlichen Repository.

Aktualisierungen dieses Profils erfolgen über diesen Fork und `personal/setup.py`, damit die persönlichen Erweiterungen erhalten bleiben. Der ursprüngliche Herdr-Quellcode und seine [Apache-2.0-Lizenz](../LICENSE) bleiben enthalten; die [Upstream-Dokumentation](../README.md) ist unverändert verfügbar.
